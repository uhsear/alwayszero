#!/usr/bin/env python
"""Name every scheduled script that cannot report failure, on a tree a parser cannot read.

Task Scheduler says Last Run Result 0x0. It has said that every night for
years. The script wraps its work in a bare except, prints a sentence into a
window nobody watches, and falls off the end of the file, so the process exits 0
whether the FTP connection opened or not:

    try:
        ftp.retrbinary("RETR extract.zip", handle.write)
        arcpy.TruncateTable_management(target)
        arcpy.Append_management(staged, target, "NO_TEST")
    except:
        print arcpy.GetMessages(2)          # a print. The process still exits 0.

This reads a file with tokenize, masks its comments and strings, walks each
handler body by indentation, and forms one verdict for the whole file: a
catch-all handler that neither re-raises nor exits, and no non-zero exit
anywhere else in the file, means the exit code is a constant. When the same file
also imports smtplib or ftplib it mails or uploads on a schedule and still
cannot say the word failed, which is the worst shape here and is ranked first.

ruff and pylint own the single handler and own it well. E722 flags a bare
except, BLE001 a blind except Exception, S110 a try/except/pass, and W0702 and
W0703 do the same for pylint. Run them. Two things are left over. They are
Python 3 parsers, so on a tree of Python 2 scripts they report syntax
diagnostics and no handler at all, while tokenize still reads most of the files.
And none of them forms a whole-file verdict: a handler is flagged, but no tool
says this file cannot exit non-zero and it emails somebody about it. pytlint has
a script mode and rule PYT023 for it, and it is the better tool on a file that
parses, but it needs an ast and an `if __name__` guard. This tool is for the
other tree. Nothing here is a reason to skip ruff on code that parses.

    python alwayszero.py --self-test
    python alwayszero.py nightly_job.py
    python alwayszero.py D:/GIS/Scripts --sites
    python alwayszero.py D:/GIS/Scripts --json

The second shape is the environment gate. A dev switch read from the environment
-- `if os.environ.get("RUN_MODE"): sys.exit(0)` -- is inherited by every child
process, so one variable set once on a workstation, in a profile or in a service
account turns the next nightly run into a job that exits 0 having done nothing.
The verdict for such a file is ENVNOOP. A switch belongs on the command line,
where a scheduler has to type it on purpose.

Exit codes: 0 no silent script and no environment gate, 1 at least one of
either, 2 nothing found but a file or directory could not be read, 64 usage
error (a missing path, or a flag the parser refuses).

EXIT CODE POLARITY. 1 means work was found, so this drives a detect-and-
remediate loop the same way stalehost does. A tree that could not be read is
never reported clean: that is what exit 2 is for.
"""

from __future__ import print_function

import argparse
import io
import json
import os
import re
import sys
import tokenize

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# Exception names that catch everything a script can throw at it. A narrower
# except is deliberate control flow; only the broad one hides a failure nobody
# chose to hide. StandardError is Python 2's near-equivalent of Exception and
# belongs here, because half of this corpus predates Python 3.
CATCH_ALL_NAMES = ("Exception", "BaseException", "StandardError")

# Imports that mean the script talks to somebody else on a schedule. A silent
# script that also does this sends a human a message that cannot say "failed".
NOTIFY_MODULES = ("smtplib", "ftplib")

# Files considered when a directory is given.
SOURCE_EXTENSIONS = (".py", ".pyw")

# Directories never walked. Vendored and virtual-environment code is not the
# subject, and one .git object tree adds minutes to a scan for nothing.
SKIP_DIRECTORIES = (".git", ".svn", "__pycache__", ".venv", "venv",
                    "site-packages", "node_modules")

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

# Verdicts, worst first. The order is the sort order of the report.
SILENT = "SILENT"        # a catch-all handler, and the exit code is a constant
REPORTS = "REPORTS"      # a catch-all handler, but the file can exit non-zero
CLEAN = "CLEAN"          # no catch-all handler
UNREADABLE = "UNREADABLE"  # tokenize refused the file
ENVNOOP = "ENVNOOP"      # an environment variable can end the run with exit 0

VERDICT_ORDER = (UNREADABLE, SILENT, ENVNOOP, REPORTS, CLEAN)

# Exit-call status, as reported per file.
NO_EXIT = "none"
ZERO_EXIT = "zero"
NONZERO_EXIT = "nonzero"


class UnreadableSource(ValueError):
    """tokenize could not read the source. Never treat this as clean."""


# --------------------------------------------------------------- the scanner

# One handler clause. The body is everything indented under it, which is why
# this is a line scanner and not a parser: a Python 2 file has no Python 3 ast
# to walk.
_EXCEPT_RE = re.compile(r"^([ \t]*)except\b([^:]*):(.*)$")

# The head of sys.exit, os._exit, the site builtins exit and quit, and
# SystemExit, up to the opening parenthesis. exit_arguments() reads the argument
# from there, with its parentheses balanced, so exit(0) can be told from
# exit(main()). The lookbehind keeps the bare-name branch off the tail of
# sys.exit.
_EXIT_RE = re.compile(
    r"(?<![\w.])(?:sys\.exit|os\._exit|exit|quit|SystemExit)\s*\(")

# A whole statement that is an exit call, with or without raise in front.
_EXITSTMT_RE = re.compile(
    r"^(?:raise\s+)?(?:sys\.exit|os\._exit|exit|quit|SystemExit)\s*\(")

# A statement that only talks and does no work: pass, a print, a log call, an
# arcpy message, a write to stdout or stderr. A gate block made of these and an
# exit(0) is a bail-out. Anything else in the block is work, and the run did it.
_INERT_RE = re.compile(
    r"^(?:pass\b|print\b|sys\.(?:stdout|stderr)\.write\s*\(|"
    r"(?:[\w.]*\.)?(?:debug|info|warning|warn|error|critical|exception|"
    r"AddMessage|AddWarning|AddError|AddIDMessage|AddReturnMessage)\s*\()")

# raise, as a statement and not as part of an identifier such as raised_error.
_RAISE_RE = re.compile(r"(?<![\w.])raise(?![\w])")

# arcpy.AddError is the cruel shape: in a toolbox it colours the Pro window red,
# in a standalone script it is a print, and the process still exits 0.
_ADDERROR_RE = re.compile(r"(?<![\w])(?:AddError|AddIDMessage|AddReturnMessage)\s*\(")

_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+(.+)$")
_FROM_RE = re.compile(r"^[ \t]*from[ \t]+([\w.]+)[ \t]+import\b")

# A read of the environment: os.environ, os.getenv, or the bare names after
# `from os import environ`. The lookbehind keeps self.environ off.
_ENV_RE = re.compile(r"(?<![\w.])(?:os\.)?(?:environ|getenv)\b")

# The literal variable name, read from the unmasked line because the masker
# blanks every string: environ["K"], environ.get("K"), getenv("K"), and
# "K" in os.environ. A name held in a variable is not recovered.
_ENVNAME_RES = (
    re.compile(r"""(?:environ(?:\.get)?|getenv)\s*[\[(]\s*(['"])([^'"]+)\1"""),
    re.compile(r"""(['"])([^'"]+)\1\s+(?:not\s+)?in\s+(?:os\.)?environ\b"""),
)

# An if or elif header, an else line, and an assignment. `==` is not an
# assignment.
_IF_RE = re.compile(r"^([ \t]*)(if|elif)\b(.*)$")
_ELSE_RE = re.compile(r"^([ \t]*)else[ \t]*:(.*)$")
_ASSIGN_RE = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*=(?!=)(.*)$")

# Arguments that mean the process ends successfully anyway. "0L" is a Python 2
# long literal and turns up in this corpus. A string is not here: sys.exit with
# a string prints it and exits 1, and the masker keeps a quote mark for it.
_ZERO_STATUS = ("", "0", "0L", "None", "False")

# Tokens that make up an f-string from Python 3.12 (PEP 701). Earlier versions
# lack the names, and there an f-string is one STRING token.
_FSTRING_PARTS = tuple(getattr(tokenize, name) for name in
                       ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
                       if hasattr(tokenize, name))
_FSTRING_START = getattr(tokenize, "FSTRING_START", -1)

# The longest if header, in lines, that is followed to its colon.
MAX_HEADER_LINES = 20


def _strip_bom(text):
    """Drop a leading UTF-8 byte order mark, the way Python does on import."""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def mask_source(text, tokenizer=tokenize.generate_tokens):
    """Return the source as lines with every comment and string body blanked.

    Blanking preserves line and column positions, so a finding still reports
    the line the reader will open. The point is that the word raise inside a
    docstring, and an except: inside a SQL string, stop being code. A string
    keeps one quote mark at its first character, so that sys.exit("message"),
    which exits 1, is not read as sys.exit(). The text of an f-string is
    blanked the same way; the expressions inside its braces stay code.

    Lines split at newline only, as tokenize counts them. str.splitlines also
    breaks at form feed, vertical tab, U+0085 and U+2028, which would put every
    mask after one of them on the wrong row.

    Raises UnreadableSource when tokenize refuses the file. Python 3.9 and 3.10
    do not raise on an unterminated one-line string: they yield the opening
    quote as an ERRORTOKEN and carry on, so that token is refused here too.
    A NUL byte is refused up front: some Python versions raise SystemError on
    one and others read past it. Python 3.13 also raises UnicodeDecodeError on
    some text with U+0085 or U+2028 in it. The tokenizer argument is for the
    self-test, which feeds it those streams.
    """
    # A Windows editor saves a BOM; Python strips it on import, tokenize
    # does not, and the first line would otherwise look like garbage.
    text = _strip_bom(text)
    if "\0" in text:
        raise UnreadableSource("source contains a NUL byte")
    grid = [list(line) for line in text.split("\n")]
    try:
        tokens = list(tokenizer(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, SystemError,
            UnicodeDecodeError) as exc:
        raise UnreadableSource(str(exc))
    for token in tokens:
        if token[0] == tokenize.ERRORTOKEN and token[1] in ("'", '"'):
            raise UnreadableSource("unterminated string literal on line %d"
                                   % token[2][0])
    for token in tokens:
        if token[0] not in (tokenize.STRING, tokenize.COMMENT) + _FSTRING_PARTS:
            continue
        (srow, scol), (erow, ecol) = token[2], token[3]
        # Python 3.13 reports negative columns for some unterminated f-strings.
        if scol < 0 or ecol < 0:
            raise UnreadableSource("the tokenizer lost its place on line %d"
                                   % srow)
        # min() rather than a bounds check: a token that ends past the grid
        # would be a tokenizer bug, and a guard here would be a branch no
        # input can reach.
        for row in range(srow, min(erow, len(grid)) + 1):
            line = grid[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            for col in range(start, min(end, len(line))):
                line[col] = " "
        if token[0] in (tokenize.STRING, _FSTRING_START):
            grid[srow - 1][scol] = '"'
    return ["".join(line) for line in grid]


def _indent(line):
    """Indent width of a line, with tabs expanded the way Python expands them."""
    stripped = line.lstrip(" \t")
    return len(line[:len(line) - len(stripped)].expandtabs(8))


def catches_everything(clause):
    """True for a bare except, and for one naming a catch-all exception.

    clause is the text between except and the colon, so it carries the Python 2
    `except IOError, exc` form as well as `except IOError as exc`.
    """
    clause = clause.strip()
    if not clause:
        return True
    clause = re.sub(r"\bas\b.*$", "", clause)
    # Python 2 binds the instance with a comma -- "except IOError, exc" -- and
    # that comma is not a tuple separator. Nothing here separates the two
    # cases, because the bound name is read as one more exception name and no
    # variable in this corpus is called Exception.
    names = [part.strip().strip("()").strip()
             for part in clause.replace("(", " ").replace(")", " ").split(",")]
    return any(name.split(".")[-1] in CATCH_ALL_NAMES for name in names if name)


def exit_arguments(text):
    """The argument text of every exit call, with its parentheses balanced.

    An unclosed call takes the rest of the text, because a block that ends
    inside a call has cut the call short, not closed it.
    """
    found = []
    for match in _EXIT_RE.finditer(text):
        depth = 1
        end = len(text)
        for pos in range(match.end(), len(text)):
            if text[pos] == "(":
                depth += 1
            elif text[pos] == ")":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        found.append(text[match.end():end])
    return found


def exit_status(text):
    """Whether this text ends the process, and with what status.

    Returns "nonzero" when any exit call carries a status that is not zero,
    "zero" when every exit call is an exit(0), and "none" when there is none.
    A file whose only exit is sys.exit(0) is the same lie as a file with no
    exit at all, and the report says which one it was. A status the scanner
    cannot evaluate, such as main() or code, counts as able to fail.
    """
    status = NO_EXIT
    for argument in exit_arguments(text):
        if argument.strip() in _ZERO_STATUS:
            status = ZERO_EXIT if status == NO_EXIT else status
        else:
            return NONZERO_EXIT
    return status


class Site(object):
    """One catch-all handler, and whether it can report the failure it caught."""

    def __init__(self, line, clause, quiet, note):
        self.line = line
        self.clause = clause
        self.quiet = quiet
        self.note = note

    def as_dict(self):
        return {"line": self.line, "clause": self.clause,
                "quiet": self.quiet, "note": self.note}


def block_after(masked, index, indent, tail):
    """The text of the block a header line opens: its tail, then deeper lines.

    A blank line does not end the block. The first non-blank line at or above
    the header's own indent does.
    """
    body = [tail]
    own = _indent(indent + "x")
    for following in masked[index + 1:]:
        if not following.strip():
            continue
        if _indent(following) <= own:
            break
        body.append(following)
    return "\n".join(body)


def find_handlers(masked):
    """Every catch-all handler in a masked source, quiet ones marked.

    A handler is quiet when its body neither re-raises nor exits non-zero.
    Printing does not count, and neither does arcpy.AddError, because in a
    standalone script AddError is a print with a colour.
    """
    sites = []
    for index, line in enumerate(masked):
        match = _EXCEPT_RE.match(line)
        if match is None:
            continue
        indent, clause, tail = match.group(1), match.group(2), match.group(3)
        if not catches_everything(clause):
            continue
        text = block_after(masked, index, indent, tail)
        reraises = bool(_RAISE_RE.search(text))
        exits = exit_status(text) == NONZERO_EXIT
        note = ""
        if not reraises and not exits and _ADDERROR_RE.search(text):
            note = ("AddError is a print in a standalone .py, not a failure; "
                    "only a toolbox turns it red")
        sites.append(Site(index + 1, " ".join(clause.split()),
                          not (reraises or exits), note))
    return sites


class Gate(object):
    """One if or elif that reads the environment and can end the run with 0."""

    def __init__(self, line, env, via):
        self.line = line
        self.env = env        # literal variable names; empty when held in a name
        self.via = via        # assigned names that carried the environment read

    def as_dict(self):
        return {"line": self.line, "env": self.env, "via": self.via}


def split_header(rest):
    """Split the text after if or elif into (condition, tail), or None.

    The split is at the first colon outside brackets that is not a walrus.
    None means the header carries on to the next line.
    """
    depth = 0
    for pos, char in enumerate(rest):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ":" and depth == 0 and rest[pos + 1:pos + 2] != "=":
            return rest[:pos], rest[pos + 1:]
    return None


def env_names(line):
    """The literal environment variable names read on one unmasked line."""
    return [match.group(2) for rx in _ENVNAME_RES for match in rx.finditer(line)]


def statements(block):
    """The logical statements of a masked block.

    A statement ends at a newline or a semicolon outside brackets, so a call
    that runs over several lines is one statement.
    """
    block = block.replace("\\\n", " ")
    found = []
    depth = 0
    start = 0
    for pos, char in enumerate(block):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char in "\n;" and depth <= 0:
            found.append(block[start:pos])
            start = pos + 1
    found.append(block[start:])
    return [text.strip() for text in found if text.strip()]


def is_bailout(block):
    """True when a block does nothing but talk and then end the run with 0.

    A block that loads data and then calls sys.exit(0) did its work, and its
    exit is the end of a normal branch, not a no-op.
    """
    if exit_status(block) != ZERO_EXIT:
        return False
    return all(_INERT_RE.match(text) or _EXITSTMT_RE.match(text)
               for text in statements(block))


class Header(object):
    """An if or elif header, with what it read from the environment."""

    def __init__(self, width, is_elif, names, via, reads):
        self.width = width
        self.is_elif = is_elif
        self.names = names    # literal variable names read by this condition
        self.via = via        # carried names the condition used
        self.reads = reads    # whether the condition reads the environment


def chain_reads(headers, masked, index):
    """The headers of the if chain that an else line closes, or None.

    Walks back over the deeper lines of the earlier branches to the elif and
    if headers at the else's own indent. Any other line at that indent --
    a for, a try, an except -- means the else belongs to something else.
    """
    width = _indent(masked[index])
    chain = []
    for back in range(index - 1, -1, -1):
        line = masked[back]
        if not line.strip() or _indent(line) > width:
            continue
        header = headers.get(back)
        if header is None or header.width != width:
            return None
        chain.append(header)
        if not header.is_elif:
            return chain
    return None


def find_gates(masked, original):
    """Every if, elif or else whose branch ends the process with status zero
    after doing no work, where the test reads the environment.

    A name assigned from an environment read carries it to a later condition,
    until the name is assigned something else. A block that can also exit
    non-zero fails loudly and is not a gate. An else branch is a gate when any
    header in its chain reads the environment, because the else runs when the
    variable is unset. `original` is the unmasked lines, for the variable
    names the masker blanked.
    """
    carried = {}      # name -> env names from the assignment that set it
    headers = {}      # line index -> Header, for the else lines to look back at
    gates = []
    consumed = -1     # last line index taken by a multi-line header
    for index, line in enumerate(masked):
        if index <= consumed:
            continue
        match = _ASSIGN_RE.match(line)
        if match is not None:
            name = match.group(1)
            if _ENV_RE.search(match.group(2)):
                carried[name] = env_names(original[index])
            else:
                carried.pop(name, None)
            continue
        match = _ELSE_RE.match(line)
        if match is not None:
            chain = chain_reads(headers, masked, index)
            if chain and any(header.reads for header in chain) and is_bailout(
                    block_after(masked, index, match.group(1), match.group(2))):
                names = set(name for header in chain for name in header.names)
                via = sorted(set(name for header in chain for name in header.via))
                gates.append(Gate(index + 1, sorted(names), via))
            continue
        match = _IF_RE.match(line)
        if match is None:
            continue
        end = index
        rest = match.group(3)
        parts = split_header(rest)
        while parts is None and end + 1 < len(masked) \
                and end - index < MAX_HEADER_LINES:
            end += 1
            rest += "\n" + masked[end]
            parts = split_header(rest)
        if parts is None:
            continue
        consumed = end
        condition, tail = parts
        direct = _ENV_RE.search(condition) is not None
        via = sorted(name for name in carried
                     if re.search(r"(?<![\w.])%s\b" % re.escape(name), condition))
        names = set()
        if direct:
            names.update(env_names("\n".join(original[index:end + 1])))
        for name in via:
            names.update(carried[name])
        reads = direct or bool(via)
        headers[index] = Header(_indent(line), match.group(2) == "elif",
                                names, via, reads)
        if reads and is_bailout(block_after(masked, end, match.group(1), tail)):
            gates.append(Gate(index + 1, sorted(names), via))
    return gates


def notifiers(masked):
    """The notification modules this source imports, in configuration order."""
    found = set()
    for line in masked:
        match = _FROM_RE.match(line)
        if match is not None:
            found.add(match.group(1).split(".")[0])
            continue
        match = _IMPORT_RE.match(line)
        if match is None:
            continue
        for part in match.group(1).split(","):
            name = part.strip().split(" as ")[0].strip()
            if name:
                found.add(name.split(".")[0])
    return [module for module in NOTIFY_MODULES if module in found]


class Report(object):
    """One file's verdict."""

    def __init__(self, name, verdict, sites=(), exits=NO_EXIT, notifies=(),
                 reason="", gates=()):
        self.name = name
        self.verdict = verdict
        self.sites = list(sites)
        self.exits = exits
        self.notifies = list(notifies)
        self.reason = reason
        self.gates = list(gates)

    @property
    def quiet_sites(self):
        return [site for site in self.sites if site.quiet]

    @property
    def gate_env(self):
        """Every literal variable name behind this file's gates, sorted."""
        return sorted(set(name for gate in self.gates for name in gate.env))

    def as_dict(self):
        return {"file": self.name, "verdict": self.verdict,
                "exits": self.exits, "notifies": self.notifies,
                "handlers": len(self.sites),
                "quiet": len(self.quiet_sites),
                "reason": self.reason,
                "sites": [site.as_dict() for site in self.sites],
                "gates": [gate.as_dict() for gate in self.gates]}


def scan_source(text, name="<source>", tokenizer=tokenize.generate_tokens):
    """The verdict for one source. Pure: no file, no network, no arcpy.

    Three conditions decide it, and they are deliberately whole-file. A script
    that can exit non-zero anywhere is trusted to do it, because a line scanner
    cannot tell which try block a given raise sits inside, and guessing wrong
    in the other direction reports a job that does work.
    """
    try:
        masked = mask_source(text, tokenizer)
    except UnreadableSource as exc:
        return Report(name, UNREADABLE, reason=str(exc))
    # ponytail: the exit test is whole-file, with no idea which try block a
    # given exit sits inside, so a sys.exit(1) the surrounding bare except
    # would swallow still clears the file. Upgrade path: when the source
    # parses, hand it to pytlint PYT023, which walks an ast and knows.
    sites = find_handlers(masked)
    exits = exit_status("\n".join(masked))
    notifies = notifiers(masked)
    gates = find_gates(masked, _strip_bom(text).split("\n"))
    quiet = [site for site in sites if site.quiet]
    if not quiet:
        verdict = CLEAN
    elif exits == NONZERO_EXIT:
        verdict = REPORTS
    else:
        verdict = SILENT
    # A gate is found whatever the handlers say, but it only sets the verdict
    # when nothing worse already has: SILENT stays SILENT and lists the gate.
    if gates and verdict in (CLEAN, REPORTS):
        verdict = ENVNOOP
    return Report(name, verdict, sites, exits, notifies, gates=gates)


def rank(report):
    """Sort key. Worst first, notifying scripts ahead of the rest."""
    return (VERDICT_ORDER.index(report.verdict),
            0 if report.notifies else 1,
            -len(report.quiet_sites),
            report.name)


# --------------------------------------------------------------- rendering

def report_line(report):
    """One line an operator can read in a grid."""
    if report.verdict == UNREADABLE:
        return "%-10s %s  %s" % (UNREADABLE, report.name, report.reason)
    notes = ""
    if report.notifies:
        notes = "  notifies=%s" % ",".join(report.notifies)
    if report.gates:
        notes += "  gates=%d env=%s" % (
            len(report.gates), ",".join(report.gate_env) or "?")
    return "%-10s %s  handlers=%d quiet=%d exit=%s%s" % (
        report.verdict, report.name, len(report.sites),
        len(report.quiet_sites), report.exits, notes)


def site_lines(report):
    """The per-handler detail under a file, for --sites."""
    out = []
    for site in report.sites:
        clause = ("except %s:" % site.clause) if site.clause else "except:"
        out.append("    line %-5d %-28s %s" % (
            site.line, clause, "QUIET" if site.quiet else "reports"))
        if site.note:
            out.append("              %s" % site.note)
    for gate in report.gates:
        out.append("    line %-5d %-28s %s" % (
            gate.line, "env gate", "NOOP env=%s%s" % (
                ",".join(gate.env) or "?",
                (" via=%s" % ",".join(gate.via)) if gate.via else "")))
    return out


def summary(reports):
    """The one line that goes in a ticket."""
    counts = {}
    for report in reports:
        counts[report.verdict] = counts.get(report.verdict, 0) + 1
    notifying = len([r for r in reports
                     if r.verdict == SILENT and r.notifies])
    return ("ALWAYSZERO silent=%d (notifying=%d) envnoop=%d reports=%d "
            "clean=%d unreadable=%d scanned=%d" % (
                counts.get(SILENT, 0), notifying, counts.get(ENVNOOP, 0),
                counts.get(REPORTS, 0), counts.get(CLEAN, 0),
                counts.get(UNREADABLE, 0), len(reports)))


# --------------------------------------------------------------------- i/o

def iter_sources(paths, walk=os.walk):
    """Every source file under the given files and directories, sorted.

    Returns (files, failures). A directory that cannot be listed is a failure,
    never a skip: a script inside it was not read. `walk` is os.walk, and the
    self-test passes a stand-in that fails and returns names out of order.
    """
    found = []
    failures = []

    def unlistable(exc):
        failures.append((exc.filename, "cannot list directory: %s" % exc.strerror))

    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in walk(path, onerror=unlistable):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRECTORIES)
                for name in sorted(files):
                    if name.lower().endswith(SOURCE_EXTENSIONS):
                        found.append(os.path.join(root, name))
        else:
            found.append(path)
    return found, failures


def read_source(path):
    """Decode a file the way a scanner must: never fail on an old encoding.

    These files were written on Windows over many years. cp1252 bytes in a
    comment are normal, and refusing to read one would turn a scanner into a
    thing that skips its own subject.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 maps all 256 byte values, so this cannot raise. The bytes it
    # invents are only ever inside a comment or a string, both of which the
    # masker blanks before anything reads them.
    return raw.decode("latin-1")


def scan_path(path):
    """Scan one file on disk. An unreadable file is a finding, never a skip."""
    try:
        text = read_source(path)
    except (IOError, OSError) as exc:
        return Report(path, UNREADABLE, reason=str(exc))
    return scan_source(text, path)


def scan_paths(paths, walk=os.walk):
    """Every report for the given files and directories, worst first."""
    files, failures = iter_sources(paths, walk)
    reports = [scan_path(path) for path in files]
    reports.extend(Report(path, UNREADABLE, reason=reason)
                   for path, reason in failures)
    return sorted(reports, key=rank)


def printable(text):
    """The text with any character stdout cannot encode written as an escape.

    A file name outside the console code page would otherwise stop the report
    with UnicodeEncodeError, and Python then exits 1: the code for a finding.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    return text.encode(encoding, "backslashreplace").decode(encoding)


# --------------------------------------------------------------- self-test

class _Harness(object):
    """check() and raises() with a count, and the footer the README quotes."""

    def __init__(self):
        self.passed = 0
        self.failed = []

    def check(self, cond, label):
        if cond:
            self.passed += 1
            print("PASS  %s" % label)
        else:
            self.failed.append(label)
            print("FAIL  %s" % label)

    def raises(self, fn, label):
        try:
            fn()
        except ValueError:
            self.check(True, label)
        except Exception as exc:
            self.check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            self.check(False, "%s (no error raised)" % label)

    def finish(self):
        print("-" * 68)
        total = self.passed + len(self.failed)
        if self.failed:
            print("%d assertions, %d failed" % (total, len(self.failed)))
            for label in self.failed:
                print("  FAILED: %s" % label)
            return 1
        print("%d assertions, 0 failed" % total)
        return 0


def self_test():
    """Assertions over the masker, the handler walk and the file verdict."""
    import contextlib
    import getpass
    import shutil
    import subprocess
    import tempfile

    harness = _Harness()
    check, raises = harness.check, harness.raises

    def capture(fn):
        """Call fn with stdout and stderr captured; return (result, output)."""
        buffer = io.StringIO()
        saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = buffer, buffer
        try:
            result = fn()
        finally:
            sys.stdout, sys.stderr = saved
        return result, buffer.getvalue()

    def run(argv):
        """main() with both streams captured, returning (exit code, output)."""
        return capture(lambda: main(argv))

    def verdict(text):
        return scan_source(text).verdict

    print("alwayszero self-test: no arcpy, no network, no corpus")
    print("-" * 68)

    # ---- the shapes that fire
    check(verdict("try:\n    go()\nexcept:\n    print('failed')\n") == SILENT,
          "a bare except whose body is only a print is silent")
    check(verdict("try:\n    go()\nexcept:\n    pass\n") == SILENT,
          "except: pass is silent")
    check(verdict("try:\n    go()\nexcept Exception as e:\n    log(e)\n") == SILENT,
          "except Exception as e with no re-raise is silent")
    check(verdict("try:\n    go()\nexcept Exception, e:\n    log(e)\n") == SILENT,
          "the python 2 comma form is read as a catch-all  <-- pinned defect")
    check(verdict("try:\n    go()\nexcept BaseException:\n    log()\n") == SILENT,
          "except BaseException is a catch-all")
    check(verdict("try:\n    go()\nexcept StandardError:\n    log()\n") == SILENT,
          "python 2 StandardError is a catch-all")
    check(verdict("try:\n    go()\nexcept (IOError, Exception):\n    log()\n")
          == SILENT, "a tuple containing Exception is a catch-all")
    check(verdict("try:\n    go()\nexcept: pass\n") == SILENT,
          "a one-line handler body on the except line is read  <-- pinned defect")
    check(verdict("try:\n    go()\nexcept: raise\n") == CLEAN,
          "a re-raise on the except line itself is read  <-- pinned defect")
    # Python 2 code reaches the catch-all through its exceptions module, so
    # the name is matched on its last dotted segment, not on the whole path.
    check(verdict("import exceptions\ntry:\n    go()\n"
                  "except exceptions.StandardError:\n    log()\n") == SILENT,
          "a dotted catch-all is matched on its last segment  <-- pinned defect")

    # ---- the shapes that do not fire
    check(verdict("try:\n    go()\nexcept IOError:\n    log()\n") == CLEAN,
          "a narrow except IOError is not a finding")
    check(verdict("try:\n    go()\nexcept (IOError, OSError):\n    log()\n")
          == CLEAN, "a tuple of narrow exceptions is not a finding")
    check(verdict("try:\n    go()\nexcept arcpy.ExecuteError:\n    log()\n")
          == CLEAN, "a dotted narrow exception is not a finding")
    check(verdict("try:\n    go()\nexcept:\n    raise\n") == CLEAN,
          "a handler that re-raises is silent about nothing")
    check(verdict("try:\n    go()\nexcept:\n    raise RuntimeError('x')\n")
          == CLEAN, "a handler that raises a new error is not quiet")
    check(verdict("import sys\ntry:\n    go()\nexcept:\n    sys.exit(1)\n")
          == CLEAN, "a handler that exits non-zero is not quiet")
    check(verdict("try:\n    go()\nexcept:\n    os._exit(2)\n") == CLEAN,
          "os._exit with a non-zero status is not quiet")
    check(verdict("try:\n    go()\nexcept:\n    raise SystemExit(3)\n") == CLEAN,
          "raise SystemExit(3) is not quiet")
    check(verdict("go()\nprint('done')\n") == CLEAN,
          "a file with no handler at all is clean")

    # ---- the exit code is the product, so exit(0) is not an exit
    report = scan_source("import sys\ntry:\n    go()\nexcept:\n"
                         "    print('failed')\nsys.exit(0)\n")
    check(report.verdict == SILENT,
          "a file whose only exit is sys.exit(0) is still silent  <-- pinned defect")
    check(report.exits == ZERO_EXIT, "the report says the exit was a zero one")
    report = scan_source("import sys\ntry:\n    go()\nexcept:\n"
                         "    print('failed')\nsys.exit(1)\n")
    check(report.verdict == REPORTS,
          "one non-zero exit elsewhere in the file clears the verdict")
    check(report.exits == NONZERO_EXIT, "the report says the exit was non-zero")
    check(len(report.quiet_sites) == 1,
          "the quiet handler is still counted on a file that can report")
    check(scan_source("try:\n    go()\nexcept:\n    log()\n").exits == NO_EXIT,
          "a file with no exit call at all reports exit=none")
    check(exit_status("sys.exit()") == ZERO_EXIT,
          "sys.exit() with no argument ends the process successfully")
    check(exit_status("sys.exit(None)") == ZERO_EXIT,
          "sys.exit(None) ends the process successfully")
    check(exit_status("sys.exit(0L)") == ZERO_EXIT,
          "the python 2 long literal 0L is a zero status")
    check(exit_status("sys.exit(0)\nsys.exit(1)") == NONZERO_EXIT,
          "one non-zero exit outranks any number of zero ones")
    check(exit_status("self.exit(1)") == NO_EXIT,
          "a method named exit on some object is not a process exit")
    check(exit_status("code = sys.exit\n") == NO_EXIT,
          "a reference to sys.exit that is never called is not an exit")
    check(scan_source("try:\n    go()\nexcept:\n    log()\n"
                      "if bad:\n    raise SystemExit(1)\n").verdict == REPORTS,
          "a raise SystemExit elsewhere in the file clears the verdict")

    # ---- an exit call is read whole: its argument may hold parentheses
    check(exit_status("sys.exit(main())") == NONZERO_EXIT,
          "sys.exit(main()) is a status the scanner cannot evaluate  <-- pinned defect")
    check(exit_status("sys.exit(int(bool(failed)))") == NONZERO_EXIT,
          "nested parentheses in an exit argument are balanced  <-- pinned defect")
    check(exit_status("sys.exit(0)\nsys.exit(f(1))") == NONZERO_EXIT,
          "a later exit with a computed status outranks an earlier zero one")
    check(exit_arguments("sys.exit(1") == ["1"] and exit_status("sys.exit(") == ZERO_EXIT,
          "an exit call cut off by the end of the block takes the rest of it")
    check(exit_status("quit(1)") == NONZERO_EXIT and exit_status("quit()") == ZERO_EXIT,
          "the site builtin quit is an exit too  <-- pinned defect")
    check(exit_status("sys.exit(False)") == ZERO_EXIT,
          "sys.exit(False) is a zero status  <-- pinned defect")
    check(exit_status("sys.exit(False or 1)") == NONZERO_EXIT,
          "an expression that merely starts with False is not zero")
    check(scan_source("import sys\ntry:\n    go()\nexcept:\n    return 1\n"
                      "sys.exit(main())\n").verdict == REPORTS,
          "a main() whose handler returns 1 and an exit through main() is not silent  <-- pinned defect")
    check(verdict("import sys\ntry:\n    go()\nexcept Exception:\n"
                  "    sys.exit('download failed')\n") == CLEAN,
          "sys.exit with a message exits 1, so the handler is not quiet  <-- pinned defect")
    check(exit_status("sys.exit('')") == NONZERO_EXIT
          and exit_status(mask_source("sys.exit('message')")[0]) == NONZERO_EXIT,
          "a masked string is still an argument, not an empty one")
    check(mask_source('x = "abc"  # tail\n')[0] == 'x = "' + " " * 12,
          "a string keeps one quote mark and a comment is blanked whole")
    check(mask_source('"""doc\nmore"""\n')[:2] == ['"' + " " * 5, " " * 7],
          "a docstring keeps the quote mark on its first row only")

    # ---- AddError, the shape that looks like reporting
    report = scan_source("import arcpy\ntry:\n    go()\nexcept:\n"
                         "    arcpy.AddError(arcpy.GetMessages(2))\n")
    check(report.verdict == SILENT,
          "AddError with no raise is still quiet  <-- pinned defect")
    check("print" in report.sites[0].note,
          "the note says AddError is a print in a standalone script")
    report = scan_source("import arcpy\ntry:\n    go()\nexcept:\n"
                         "    arcpy.AddError('x')\n    raise\n")
    check(report.sites[0].note == "",
          "a handler that re-raises after AddError carries no note")
    check(scan_source("import arcpy\ntry:\n    go()\nexcept:\n"
                      "    arcpy.AddIDMessage('ERROR', 12)\n").sites[0].note != "",
          "AddIDMessage carries the same note as AddError")

    # ---- the log is not the exit code
    # ruff BLE001 exempts a handler that logs the exception, which is the right
    # call for a library and the wrong one for a scheduled job: the traceback
    # lands in a file and the process still ends 0. Two files in the tree this
    # was built from are silent for exactly this reason and ruff passes them.
    check(verdict("import logging\ntry:\n    go()\nexcept Exception as e:\n"
                  "    logging.error('%s', e, exc_info=True)\n") == SILENT,
          "logging the traceback does not make a script able to fail  <-- pinned defect")
    check(verdict("import traceback\ntry:\n    go()\nexcept:\n"
                  "    traceback.print_exc()\n") == SILENT,
          "printing the traceback does not make a script able to fail")

    # ---- masking: a comment or a string is not code
    check(verdict("try:\n    go()\nexcept:\n    log()  # raise it later\n")
          == SILENT, "the word raise in a comment does not excuse a handler")
    check(verdict('try:\n    go()\nexcept:\n    """raise me"""\n    log()\n')
          == SILENT, "the word raise in a docstring does not excuse a handler")
    check(verdict('sql = "except:\\n    pass"\ngo()\n') == CLEAN,
          "an except clause inside a string is not a handler")
    check(verdict('log("sys.exit(1)")\ntry:\n    go()\nexcept:\n    log()\n')
          == SILENT, "an exit call inside a string does not clear the file")
    check(scan_source("# comment\n\ntry:\n    go()\nexcept:\n    log()\n"
                      ).sites[0].line == 5,
          "masking keeps the line number the reader will open")
    check(verdict("\ufefftry:\n    go()\nexcept:\n    log()\n") == SILENT,
          "a leading UTF-8 byte order mark is not a syntax error  <-- pinned defect")
    # tokenize accepts the mark, so the line above passes with or without the
    # strip. This one does not: the mark sits in front of the import, and the
    # import scanner anchors on the start of the line.
    check(scan_source("\ufeffimport smtplib\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["smtplib"],
          "the import on the line the mark sits on is still an import  <-- pinned defect")

    # Python 3.12 splits an f-string into tokens that are not STRING.
    check(verdict('try:\n    go()\nexcept Exception as e:\n'
                  '    log(f"failed, will raise an alert: {e}")\n') == SILENT,
          "the word raise inside an f-string is not code  <-- pinned defect")
    check(verdict('try:\n    go()\nexcept Exception as e:\n'
                  '    log(f"{{raise}} {e}")\n') == SILENT,
          "a doubled brace in an f-string does not unmask its text  <-- pinned defect")
    check(verdict('try:\n    go()\nexcept Exception as e:\n'
                  '    log(f"""one\n raise {e}\n""")\n') == SILENT,
          "a triple-quoted f-string over several rows is masked  <-- pinned defect")
    check(verdict('import sys\ntry:\n    go()\nexcept Exception as e:\n'
                  '    sys.exit(f"failed {e}")\n') == CLEAN,
          "sys.exit with an f-string message exits 1  <-- pinned defect")
    check(verdict('x = f"except:\\n    pass"\ngo()\n') == CLEAN,
          "an except clause inside an f-string is not a handler")
    # str.splitlines breaks at more characters than tokenize counts as a line
    # end, so every mask after one of them landed a row too early.
    check(verdict("import sys\ntry:\n    go()\nexcept:\n    log()  # raise\n"
                  "\x0c\n# page two\n") == SILENT,
          "a form feed does not shift the masks  <-- pinned defect")
    check(verdict("\x0c\n# page one\ntry:\n    go()\nexcept:\n"
                  "    log()  # raise later\n") == SILENT,
          "a form feed before the handler does not hide it  <-- pinned defect")
    check(verdict("# a\u2028b\n# c\ntry:\n    go()\nexcept:\n    log()  # raise\n")
          == SILENT, "U+2028 in a comment does not shift the masks  <-- pinned defect")
    check(scan_source("\x0c\ntry:\n    go()\nexcept:\n    log()\n"
                      ).sites[0].line == 4,
          "the line number counts a form feed row as tokenize does")
    raises(lambda: mask_source("x = 1\0\n"),
           "a NUL byte is unreadable on every Python  <-- pinned defect")
    check(scan_source("x = 1\0\n", "n.py").verdict == UNREADABLE
          and "NUL" in scan_source("\0", "n.py").reason,
          "a file with a NUL byte, as UTF-16 text has, is UNREADABLE")

    def crashing_stream(readline):
        raise SystemError("tokenizer crashed")

    raises(lambda: mask_source("x = 1\n", crashing_stream),
           "a SystemError from the tokenizer is unreadable  <-- pinned defect")

    def undecodable_stream(readline):
        raise UnicodeDecodeError("utf-8", b"\xe2", 0, 1, "unexpected end of data")

    raises(lambda: mask_source("x = 1\n", undecodable_stream),
           "a UnicodeDecodeError from the tokenizer is unreadable  <-- pinned defect")

    def lost_stream(readline):
        yield (tokenize.STRING, '"a"', (2, -3), (2, 0), '"a"\n')
    raises(lambda: mask_source('x\n"a"\n', lost_stream),
           "a token with a negative column, as python 3.13 gives an unterminated f-string, is unreadable  <-- pinned defect")
    check(scan_source("class A:  x=f'{a deE xception), e:\n   f f(self):\n"
                      "    try:\n      x=f'{a!r:>else:{w}} raise'\n"
                      "    expt (IOError, E xception), e:\n      pass\n").verdict
          in (CLEAN, UNREADABLE),
          "an unterminated f-string never crashes the scan  <-- pinned defect")

    def dollar_stream(readline):
        yield (tokenize.ERRORTOKEN, "$", (1, 4), (1, 5), "x = $\n")
    check(mask_source("x = $\n", dollar_stream) == ["x = $", ""],
          "an ERRORTOKEN that is not a quote is not unreadable")

    # ---- python 2, the tree this was built for
    source = "try:\n    print 'working'\nexcept:\n    print 'failed'\n"
    check(verdict(source) == SILENT,
          "a python 2 print statement is scanned, not skipped")
    import ast as _ast
    parsed = True
    try:
        _ast.parse(source)
    except SyntaxError:
        parsed = False
    check(parsed is False,
          "that same fixture does not parse as python 3  <-- pinned defect")

    # ---- unreadable is never clean
    raises(lambda: mask_source('x = "unterminated\n'),
           "an unterminated string raises UnreadableSource")
    # Python 3.9 and 3.10 yield the opening quote as an ERRORTOKEN instead of
    # raising. Feed that stream in so every host takes the branch.
    def quote_stream(readline):
        yield (tokenize.ERRORTOKEN, '"', (1, 4), (1, 5), 'x = "oops\n')
    raises(lambda: mask_source('x = "oops\n', quote_stream),
           "an ERRORTOKEN quote, as python 3.9 yields it, is unreadable  <-- pinned defect")
    check("on line 1" in scan_source('x = "oops\n', "q.py", quote_stream).reason,
          "that report names the row of the quote, not its column")
    report = scan_source('x = "unterminated\n', "broken.py")
    check(report.verdict == UNREADABLE,
          "a file tokenize cannot read is UNREADABLE, never CLEAN")
    check(report.reason != "", "the unreadable report carries the reason")
    check(report.as_dict()["quiet"] == 0,
          "an unreadable file reports no quiet handlers")

    # ---- the body walk
    report = scan_source("try:\n    go()\nexcept:\n    log()\nraise Error()\n")
    check(report.verdict == SILENT,
          "a raise after the dedent is not inside the handler  <-- pinned defect")
    report = scan_source("try:\n    go()\nexcept:\n    log()\n\n    raise\n")
    check(report.verdict == CLEAN,
          "a blank line does not end the handler body  <-- pinned defect")
    check(verdict("try:\n    go()\nexcept:\n    raised_error = True\n") == SILENT,
          "a variable named raised_error is not a re-raise  <-- pinned defect")
    report = scan_source("try:\n    a()\nexcept:\n    log()\ntry:\n    b()\n"
                         "except:\n    pass\n")
    check(len(report.sites) == 2, "two handlers are counted as two sites")
    report = scan_source("class J:\n    def run(self):\n        try:\n"
                         "            go()\n        except:\n"
                         "            self.log()\n")
    check(report.verdict == SILENT, "a handler nested in a class is found")
    report = scan_source("try:\n    go()\nexcept:\n\ttab_indented()\n")
    check(report.verdict == SILENT, "a tab-indented handler body is read")
    report = scan_source("try:\n    go()\nexcept:\n    try:\n        b()\n"
                         "    except:\n        raise\n")
    check(len(report.sites) == 2 and report.sites[0].quiet is False,
          "a nested handler that re-raises clears the handler around it")

    # ---- the composite: the notification import
    report = scan_source("import smtplib\ntry:\n    go()\nexcept:\n    log()\n")
    check(report.notifies == ["smtplib"], "import smtplib is seen")
    check(scan_source("import os, ftplib\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["ftplib"],
          "a module in a multi-name import line is seen")
    check(scan_source("from smtplib import SMTP\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["smtplib"], "a from-import is seen")
    check(scan_source("from ftplib.x import Y\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["ftplib"],
          "a from-import of a submodule names its root package")
    check(scan_source("import ftplib as f\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["ftplib"], "an aliased import is seen")
    check(scan_source("import smtplib, ftplib\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["smtplib", "ftplib"],
          "both notification modules are listed in configuration order")
    check(scan_source('log("import smtplib")\ngo()\n').notifies == [],
          "an import named inside a string is not an import")
    check(scan_source("import smtplib,\ntry:\n go()\nexcept:\n log()\n"
                      ).notifies == ["smtplib"],
          "a trailing comma in an import line is not a module name")

    # ---- ranking and rendering
    # The mailer is named last in the alphabet on purpose. Both files are
    # SILENT with one quiet handler each, so name is the only other tie-break,
    # and a name that sorted first would pass this whether rank weighed the
    # notification import or not.
    quiet_mailer = scan_source("import smtplib\ntry:\n go()\nexcept:\n log()\n",
                               "zz_mailer.py")
    quiet_plain = scan_source("try:\n go()\nexcept:\n log()\n", "plain.py")
    clean = scan_source("go()\n", "fine.py")
    order = [r.name for r in sorted([clean, quiet_plain, quiet_mailer], key=rank)]
    check(order == ["zz_mailer.py", "plain.py", "fine.py"],
          "a silent script that notifies is ranked above one that does not")
    two = scan_source("try:\n a()\nexcept:\n log()\ntry:\n b()\nexcept:\n log()\n",
                      "zz_two.py")
    check([r.name for r in sorted([quiet_plain, two], key=rank)]
          == ["zz_two.py", "plain.py"],
          "more quiet handlers rank first, ahead of the name")
    check(report_line(Report("u.py", UNREADABLE, reason="why"))
          == "UNREADABLE u.py  why",
          "an unreadable file renders as verdict, name and reason, nothing else")
    check(_indent("\tx") == 8 and _indent("  \tx") == 8
          and _indent("    x") == 4,
          "a tab advances to the next multiple of eight, as python counts")
    check(report_line(quiet_mailer).startswith(SILENT),
          "the report line starts with the verdict")
    check("notifies=smtplib" in report_line(quiet_mailer),
          "the report line names the notification module")
    check("notifies" not in report_line(quiet_plain),
          "a file that notifies nobody has no notifies column")
    check(report_line(scan_source('x = "oops\n', "b.py")).startswith(UNREADABLE),
          "an unreadable file renders its verdict too")
    lines = site_lines(quiet_mailer)
    check(lines[0].strip().startswith("line 4"),
          "a site line names the handler line number")
    check("QUIET" in lines[0], "a quiet site is labelled QUIET")
    check("reports" in site_lines(scan_source(
        "try:\n go()\nexcept:\n raise\n"))[0],
        "a handler that re-raises is listed, and labelled reports")
    noted = site_lines(scan_source("try:\n go()\nexcept:\n arcpy.AddError('x')\n"))
    check(len(noted) == 2 and "AddError" in noted[1],
          "the AddError note is printed under its own site")
    check("scanned=3" in summary([clean, quiet_plain, quiet_mailer]),
          "the summary counts every file scanned")
    check("silent=2 (notifying=1)" in summary([clean, quiet_plain, quiet_mailer]),
          "the summary separates the silent scripts that notify")

    # ---- the environment gate: an inherited variable ends the run with 0
    gate = scan_source("import os, sys\nif os.environ.get('RUN_MODE'):\n"
                       "    sys.exit(0)\nwork()\n")
    check(gate.verdict == ENVNOOP,
          "an environment read that ends the run with exit 0 is ENVNOOP")
    check(len(gate.gates) == 1 and gate.gates[0].line == 2,
          "the gate names the line of the if")
    check(gate.gate_env == ["RUN_MODE"], "the gate names the variable")
    check(gate.gates[0].via == [], "a direct read carries no via name")
    check(scan_source("if os.getenv('DRY'): sys.exit()\nwork()\n").verdict
          == ENVNOOP, "a one-line gate with os.getenv and bare sys.exit() is read")
    check(scan_source("if not os.environ.get('ENABLE'):\n    print('off')\n"
                      "    sys.exit(0)\nwork()\n").verdict == ENVNOOP,
          "a missing variable that ends the run is a gate too")
    check(scan_source("if os.environ['K']:\n    exit(0)\n").gate_env == ["K"],
          "an os.environ subscript names its variable")
    check(scan_source("if 'K' in os.environ:\n    exit(0)\n").gate_env == ["K"],
          "a membership test names its variable")
    check(scan_source("if 'K' not in os.environ:\n    exit(0)\n").gate_env
          == ["K"], "a negated membership test names its variable")
    check(scan_source("from os import environ\nif environ.get('K'):\n"
                      "    exit(0)\n").verdict == ENVNOOP,
          "a bare environ after from os import environ is read")
    check(scan_source("if a:\n    pass\nelif os.environ.get('K'):\n"
                      "    exit(0)\n").gates[0].line == 3,
          "an elif is a gate too")
    check(scan_source("if (m := os.environ.get('K')):\n    exit(0)\n").verdict
          == ENVNOOP, "a parenthesised walrus condition is read")
    check(scan_source("if m := os.environ.get('K'):\n    exit(0)\n").gate_env
          == ["K"], "an unparenthesised walrus is not taken for the colon")
    check(scan_source("if os.environ.get(KEY):\n    exit(0)\n").gate_env == [],
          "a variable name held in a name is not recovered")
    check(scan_source("if cache[1:2] or os.getenv('K'):\n    exit(0)\n"
                      ).gate_env == ["K"],
          "a colon inside brackets does not end the condition")

    # An assigned name carries the read to a later condition.
    gate = scan_source("DEV = os.environ.get('MODE_X')\nwork_setup()\n"
                       "if DEV:\n    sys.exit(0)\n")
    check(gate.verdict == ENVNOOP and gate.gates[0].via == ["DEV"],
          "a name assigned from the environment carries the gate to its if")
    check(gate.gate_env == ["MODE_X"],
          "the carried variable name comes from the assignment")
    check(scan_source("A = os.getenv('AAA')\nif A or os.getenv('BBB'):\n"
                      "    exit(0)\n").gate_env == ["AAA", "BBB"],
          "a direct read and a carried one are both named, sorted")
    check(scan_source("DEV = os.environ.get('D')\nDEV = '--dev' in sys.argv\n"
                      "if DEV:\n    exit(0)\n").verdict == CLEAN,
          "a name reassigned from the command line is not a gate  <-- pinned defect")
    check(scan_source("DEV = os.environ.get('D')\nif DEVICE:\n    exit(0)\n"
                      ).verdict == CLEAN,
          "a longer name that starts like the carried one is not it  <-- pinned defect")
    check(scan_source("DEV = os.environ.get('D')\nif cfg.DEV:\n    exit(0)\n"
                      ).verdict == CLEAN,
          "an attribute with the carried name is not it  <-- pinned defect")
    check(scan_source("x = 1\nif x == 1:\n    exit(0)\n").verdict == CLEAN,
          "an == comparison is not an assignment and not an environment read")

    # The shapes that look like a gate and are not one.
    check(scan_source("if os.environ.get('K'):\n    sys.exit(1)\n").verdict
          == CLEAN, "a gate that fails loudly is not a no-op")
    check(scan_source("if os.environ.get('K'):\n    exit(0)\n    exit(1)\n"
                      ).verdict == CLEAN,
          "a gate body that can also exit non-zero is not a no-op")
    check(scan_source("if os.environ.get('K'):\n    log('dev')\nwork()\n"
                      ).verdict == CLEAN, "an environment read with no exit is not a gate")
    check(scan_source("if done:\n    sys.exit(0)\n").verdict == CLEAN,
          "an exit(0) behind a condition that never reads the environment is fine")
    check(scan_source("home = os.environ.get('K')\nsys.exit(0)\n").verdict
          == CLEAN, "an environment read next to an unconditional exit is fine")
    check(scan_source("# if os.environ.get('K'): exit(0)\n"
                      "s = 'if os.environ.get(\"K\"): exit(0)'\n").verdict
          == CLEAN, "a gate inside a comment or a string is not code")
    check(scan_source("if self.environ.get('K'):\n    exit(0)\n").verdict
          == CLEAN, "self.environ is not the process environment  <-- pinned defect")
    gate = scan_source("if (os.environ.get('RUN_MODE') and\n"
                       "        not args):\n    sys.exit(0)\nwork()\n")
    check(gate.verdict == ENVNOOP and gate.gates[0].line == 1
          and gate.gate_env == ["RUN_MODE"],
          "a condition split across lines is read, at the line of the if  <-- pinned defect")
    check(scan_source("t = 1\nif (t ==\n        2):\n    exit(0)\n").verdict
          == CLEAN, "a split condition that never reads the environment is fine")
    check(scan_source("if f(a,\n     flag=os.environ.get('Z')):\n    pass\n"
                      "if flag:\n    exit(0)\n").verdict == CLEAN,
          "the last row of a split header is not read again as an assignment")

    def split_header_source(extra):
        return ("if (os.environ.get('K')" + " and\n    x" * extra
                + "):\n    exit(0)\n")

    check(scan_source(split_header_source(MAX_HEADER_LINES)).verdict == ENVNOOP
          and scan_source(split_header_source(MAX_HEADER_LINES + 1)).verdict
          == CLEAN,
          "a header is followed for MAX_HEADER_LINES rows and no further")
    check(scan_source("if x\n" * 30).verdict == CLEAN,
          "a header with no colon is given up on, not followed to the end")
    check(scan_source("os.environ.get('K')\nif x").verdict == CLEAN,
          "a header cut off by the end of the file is given up on")
    check(scan_source("os.environ.get('K') and sys.exit(0)\n").verdict == CLEAN,
          "a gate written as one expression is not read  <-- known limit")
    check(scan_source("if os.environ.get('K'):\n    raise SystemExit\n"
                      ).verdict == CLEAN,
          "a bare raise SystemExit is not counted as an exit  <-- known limit")
    check(scan_source("t = os.environ.get('K')\nf(\n    t=1,\n)\nif t:\n"
                      "    exit(0)\n").verdict == CLEAN,
          "a keyword argument line is read as an assignment  <-- known limit")

    # The else branch of an environment test runs when the variable is unset.
    gate = scan_source("if os.environ.get('ENABLE_LOAD'):\n    load()\n"
                       "else:\n    sys.exit(0)\n")
    check(gate.verdict == ENVNOOP and gate.gates[0].line == 3
          and gate.gate_env == ["ENABLE_LOAD"],
          "an exit(0) in the else of an environment test is a gate  <-- pinned defect")
    check(scan_source("if a:\n    pass\nelif os.getenv('K'):\n    go()\n\n"
                      "else:\n    print('off')\n    exit(0)\n").gate_env == ["K"],
          "an elif that reads the environment makes the else a gate")
    gate = scan_source("D = os.getenv('K')\nif D:\n    go()\nelse:\n    exit()\n")
    check(gate.gates[0].via == ["D"] and gate.gate_env == ["K"],
          "an else is a gate through a carried name too")
    check(scan_source("def f():\n    if os.environ.get('K'):\n        go()\n"
                      "    else:\n        exit(0)\n").verdict == ENVNOOP,
          "an else inside a function is read")
    check(scan_source("if os.environ.get('K'):\n    if x:\n        go()\n"
                      "else:\n    exit(0)\n").verdict == ENVNOOP,
          "an else after a nested if belongs to the outer if")
    check(scan_source("if a:\n    go()\nelse:\n    exit(0)\n").verdict == CLEAN,
          "an else of a test that never reads the environment is fine")
    check(scan_source("if os.environ.get('K'):\n    go()\nelse:\n    load()\n"
                      "    exit(0)\n").verdict == CLEAN,
          "an else that does work before exit(0) is not a no-op")
    check(scan_source("if os.environ.get('K'):\n    go()\nfor x in y:\n"
                      "    go()\nelse:\n    exit(0)\n").verdict == CLEAN,
          "the else of a for loop is not the else of an earlier if")
    check(scan_source("if os.environ.get('K'):\n    go()\ntry:\n    go()\n"
                      "except E:\n    pass\nelse:\n    exit(0)\n").verdict == CLEAN,
          "the else of a try is not the else of an earlier if")
    check(scan_source("else:\n    exit(0)\n").verdict == CLEAN,
          "an else with nothing before it is not a gate")

    # A gate is a bail-out. A branch that works and then exits 0 is ordinary.
    check(scan_source("if os.environ.get('USE_SDE'):\n    load_from_sde()\n"
                      "    sys.exit(0)\nload_from_files()\n").verdict == CLEAN,
          "a gate block that does the work before exit(0) is not a no-op  <-- pinned defect")
    check(scan_source("if os.environ.get('K'): load(); sys.exit(0)\n").verdict
          == CLEAN, "work and an exit on one line is work")
    for talk in ("print('x')", "print 'x'", "pass", "logging.info('x')",
                 "log.warning('x')", "logger.error('x')", "arcpy.AddMessage('x')",
                 "arcpy.AddWarning('x')", "sys.stderr.write('x')",
                 "print('x'); print('y')", "print('x',\n          'y')"):
        check(scan_source("if os.environ.get('K'):\n    %s\n    exit(0)\n"
                          % talk).verdict == ENVNOOP,
              "a gate block that only talks is a gate: %s" % talk.split("\n")[0])
    check(scan_source("if os.environ.get('K'):\n    print('x'); sys.exit(0)\n"
                      ).verdict == ENVNOOP,
          "a print and an exit on one line is a gate")
    check(scan_source("if os.environ.get('K'):\n    print 'a', \\\n        'b'\n"
                      "    quit()\n").verdict == ENVNOOP,
          "a backslash continuation is one statement")
    check(scan_source("if os.environ.get('K'):\n    print_report()\n"
                      "    exit(0)\n").verdict == CLEAN,
          "print_report() is work, not a print  <-- pinned defect")
    check(statements("a(1,\n2); b") == ["a(1,\n2)", "b"],
          "a statement ends at a newline or a semicolon outside brackets")
    gate = scan_source("if os.getenv('DDD') or os.getenv('BBB') or os.getenv('CCC')"
                       " or os.getenv('AAA'):\n    exit(0)\n")
    check(gate.gates[0].env == ["AAA", "BBB", "CCC", "DDD"]
          and gate.gate_env == ["AAA", "BBB", "CCC", "DDD"],
          "a gate and its report list the variables sorted")
    check(scan_source("B = os.getenv('X')\nA = os.getenv('Y')\nif A or B:\n"
                      "    exit(0)\n").gates[0].via == ["A", "B"],
          "a gate lists the names that carried the read sorted, not in assignment order")
    gate = scan_source("A = os.getenv('AAA')\nB = os.getenv('BBB')\nif B:\n"
                       "    go()\nelif A:\n    go()\nelse:\n    exit()\n").gates[0]
    check(gate.via == ["A", "B"] and gate.env == ["AAA", "BBB"],
          "an else gate lists the variables and carrying names of the whole chain sorted")

    # Gate and handlers together.
    gate = scan_source("import sys\nif os.environ.get('K'):\n    sys.exit(0)\n"
                       "try:\n    go()\nexcept:\n    log()\n")
    check(gate.verdict == SILENT and len(gate.gates) == 1,
          "SILENT stays SILENT and still lists the gate")
    gate = scan_source("if os.environ.get('K'):\n    exit(0)\ntry:\n    go()\n"
                       "except:\n    log()\nif bad:\n    raise SystemExit(1)\n")
    check(gate.verdict == ENVNOOP and len(gate.quiet_sites) == 1,
          "a gate outranks REPORTS, and the quiet handler is still counted")
    check(scan_source("if os.environ.get('K'):\n    exit(0)\nif bad:\n"
                      "    raise SystemExit(1)\n").verdict == ENVNOOP,
          "a non-zero exit elsewhere does not clear a gate  <-- pinned defect")

    # ---- ranking and rendering of a gate
    order = [r.name for r in sorted([
        scan_source("go()\n", "c.py"),
        scan_source("if os.getenv('K'): exit(0)\n", "g.py"),
        scan_source("try:\n go()\nexcept:\n log()\n", "s.py"),
        scan_source("try:\n go()\nexcept:\n log()\nexit(1)\n", "r.py")],
        key=rank)]
    check(order == ["s.py", "g.py", "r.py", "c.py"],
          "ENVNOOP ranks below SILENT and above REPORTS")
    gate = scan_source("DEV = os.environ.get('MODE_X')\nif DEV:\n    exit(0)\n",
                       "g.py")
    check(report_line(gate).startswith(ENVNOOP)
          and "gates=1 env=MODE_X" in report_line(gate),
          "the report line names the gate count and the variable")
    check("env=?" in report_line(scan_source(
        "if os.environ.get(KEY):\n    exit(0)\n", "g.py")),
        "a gate whose variable name is not literal prints env=?")
    check("gates=" not in report_line(scan_source("go()\n", "c.py")),
          "a file without a gate has no gates column")
    lines = site_lines(gate)
    check(len(lines) == 1 and "NOOP env=MODE_X via=DEV" in lines[0],
          "a site line names the gate, its variable and the carrying name")
    check("NOOP env=K" in site_lines(scan_source(
        "if os.getenv('K'): exit(0)\n"))[0] and "via" not in site_lines(
        scan_source("if os.getenv('K'): exit(0)\n"))[0],
        "a direct gate has no via column")
    check("envnoop=1" in summary([gate]), "the summary counts ENVNOOP files")
    check(gate.as_dict()["gates"] == [
        {"line": 2, "env": ["MODE_X"], "via": ["DEV"]}],
        "the JSON record carries the gate detail")

    # ---- argument handling
    args = _parse(["a.py"])
    check(args.paths == ["a.py"], "a path is read positionally")
    check(args.json is False, "--json defaults to OFF")
    check(args.sites is False, "--sites defaults to OFF")
    check(args.self_test is False, "--self-test defaults to OFF")
    check(_parse(["--self-test"]).self_test, "--self-test parses")
    check(_parse(["a.py", "--json"]).json, "--json parses")
    check(_parse(["a.py", "--sites"]).sites, "--sites parses")
    check(_parse(["a.py", "b.py"]).paths == ["a.py", "b.py"],
          "several paths are read")

    def refused(argv):
        """True when the parser exits on argv instead of accepting it."""
        saved = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _parse(argv)
        except SystemExit:
            return True
        finally:
            sys.stderr = saved
        return False

    check(refused(["a.py", "--self"]),
          "a unique prefix of --self-test is refused  <-- pinned defect")
    check(refused(["a.py", "--self-te"]),
          "a longer unique prefix of --self-test is refused  <-- pinned defect")
    # This tool never writes, so there is no --apply. Pin that nobody adds a
    # write flag without also pinning its prefixes.
    check(refused(["a.py", "--ap"]) and refused(["a.py", "--apply"]),
          "there is no write flag, and neither --apply nor a prefix of it parses  <-- pinned defect")
    check(refused(["a.py", "--js"]) and refused(["a.py", "--si"]),
          "prefixes of --json and --sites are refused too")
    check(not refused(["a.py"]), "a plain path is accepted")

    def refusal_code(argv):
        """The exit code the parser gives argv, or None when it accepts it."""
        saved = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _parse(argv)
        except SystemExit as exc:
            return exc.code
        finally:
            sys.stderr = saved
        return None

    check(refusal_code(["--bogus", "a.py"]) == 64,
          "an unknown flag is a usage error, exit 64, not 2  <-- pinned defect")
    check(refusal_code(["a.py", "--ap"]) == 64 and refusal_code(["--js"]) == 64,
          "a refused prefix is exit 64 too  <-- pinned defect")
    check(refusal_code(["a.py"]) is None, "an accepted command line has no refusal code")

    # ---- a file name the console cannot hold
    # The stream is strict ASCII, as a redirected cp1252 console is for a CJK
    # name. print would raise UnicodeEncodeError and the run would exit 1.
    def ascii_stream():
        return io.TextIOWrapper(io.BytesIO(), encoding="ascii")

    saved = sys.stdout
    sys.stdout = ascii_stream()
    try:
        shown = printable("caf\u00e9_\u03a9.py")
    finally:
        sys.stdout = saved
    check(shown == "caf\\xe9_\\u03a9.py",
          "a character the output encoding cannot hold is written as an escape  <-- pinned defect")
    check(printable("plain.py") == "plain.py" and printable("caf\u00e9") == "caf\u00e9",
          "text the stream can encode is left alone")
    root = tempfile.mkdtemp(prefix="alwayszero-name-")
    try:
        name = os.path.join(root, "caf\u00e9_\u03a9.py")
        with open(name, "w") as handle:
            handle.write("try:\n    go()\nexcept:\n    pass\n")
        stream = ascii_stream()
        saved = sys.stdout
        sys.stdout = stream
        try:
            code = main([root, "--sites"])
            stream.flush()
            shown = stream.buffer.getvalue().decode("ascii")
        finally:
            sys.stdout = saved
        check(code == 1 and "caf\\xe9_\\u03a9.py" in shown and "silent=1" in shown,
              "a report on a file with an unencodable name still prints and exits 1  <-- pinned defect")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- a directory that cannot be listed is a finding, never a skip
    seen = {}

    def stub_walk(top, onerror):
        onerror(OSError(13, "Permission denied", os.path.join(top, "locked")))
        dirs = [".git", ".svn", "__pycache__", ".venv", "venv", "site-packages",
                "node_modules", "zz", "aa"]
        yield top, dirs, ["b.py", "a.py", "c.PY", "d.pyw", "e.txt"]
        seen["dirs"] = list(dirs)

    here = os.path.dirname(os.path.abspath(__file__))
    files, failures = iter_sources([here], stub_walk)
    check(files == [os.path.join(here, name)
                    for name in ("a.py", "b.py", "c.PY", "d.pyw")],
          "files come back sorted, with .pyw and an upper-case .PY, and no .txt")
    check(seen["dirs"] == ["aa", "zz"],
          "every skipped directory is pruned and the rest are sorted")
    check(failures == [(os.path.join(here, "locked"),
                        "cannot list directory: Permission denied")],
          "a directory the walk could not list is returned as a failure  <-- pinned defect")
    files, failures = iter_sources([os.path.join(here, "single.py")], stub_walk)
    check(files == [os.path.join(here, "single.py")] and failures == [],
          "a path that is not a directory is scanned as a file")

    def refusing_walk(top, onerror):
        onerror(OSError(13, "Permission denied", top))
        return iter(())

    reports = scan_paths([here], refusing_walk)
    check(len(reports) == 1 and reports[0].verdict == UNREADABLE
          and reports[0].name == here and "Permission denied" in reports[0].reason,
          "an unlistable root is one UNREADABLE report, so the run cannot be clean  <-- pinned defect")

    def lists(path):
        with contextlib.suppress(OSError):
            os.listdir(path)
            return True
        return False

    # Deny the listing for real: chmod 0 on POSIX, an icacls deny ACE on
    # Windows. Neither call is conditional, so the count is the same on every
    # host. An account that can list anyway (root) leaves blocked False, and the
    # assertion is then vacuous; the stub assertions above still cover the code.
    root = tempfile.mkdtemp(prefix="alwayszero-deny-")
    locked = os.path.join(root, "locked")
    user = getpass.getuser()

    def acl(*args):
        with contextlib.suppress(OSError):
            with open(os.devnull, "w") as sink:
                subprocess.call(("icacls", locked) + args, stdout=sink, stderr=sink)

    try:
        os.mkdir(locked)
        with open(os.path.join(locked, "nightly.py"), "w") as handle:
            handle.write("try:\n    go()\nexcept:\n    pass\n")
        with open(os.path.join(root, "ok.py"), "w") as handle:
            handle.write("go()\n")
        listed = lists(locked)
        os.chmod(locked, 0)
        acl("/deny", "%s:(RD,X)" % user)
        blocked = not lists(locked)
        code, output = run([root])
        check(listed and ((not blocked) or (
            code == 2 and "UNREADABLE" in output and "locked" in output
            and "unreadable=1" in output)),
              "a directory the account cannot list is UNREADABLE and exits 2  <-- pinned defect")
    finally:
        acl("/remove:d", user)
        with contextlib.suppress(OSError):
            os.chmod(locked, 0o700)
        shutil.rmtree(root, ignore_errors=True)

    # ---- the summary counts, one of each verdict
    root = tempfile.mkdtemp(prefix="alwayszero-counts-")
    try:
        for name, body in (
                ("silent.py", "try:\n    go()\nexcept:\n    pass\n"),
                ("gated.py", "if os.environ.get('K'):\n    exit(0)\n"),
                ("reports.py", "try:\n    go()\nexcept:\n    pass\nexit(1)\n"),
                ("clean.py", "go()\n"),
                ("also_clean.py", "go()\n"),
                ("broken.py", 'x = "oops\n')):
            with open(os.path.join(root, name), "w") as handle:
                handle.write(body)
        code, output = run([root, "--json"])
        check(json.loads(output)["summary"] == {
            "silent": 1, "envnoop": 1, "reports": 1, "clean": 2,
            "unreadable": 1, "scanned": 6},
            "--json counts every verdict separately")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- the README sample, rebuilt and compared line for line
    sample = {
        "mail_summary.py": [
            "import smtplib", "import sys", "", "", "def send(body):",
            '    server = smtplib.SMTP("localhost")',
            '    server.sendmail("jobs@example.org", ["team@example.org"], body)',
            "", "", "try:", '    send("nightly summary")',
            "except Exception as exc:", '    print("mail failed: %s" % exc)',
            "sys.exit(0)"],
        "nightly_extract.py": [
            "import ftplib", "import arcpy", "", "try:",
            '    ftp = ftplib.FTP("ftp.example.org")',
            '    arcpy.TruncateTable_management("target")',
            '    arcpy.Append_management("staged", "target", "NO_TEST")',
            "except:", "    arcpy.AddError(arcpy.GetMessages(2))"],
        "gated_load.py": [
            "import os", "import sys", "", 'DEV_MODE = os.environ.get("LOAD_DEV_MODE")',
            "if DEV_MODE:", '    print("dev mode, nothing loaded")',
            "    sys.exit(0)", "load_roads()"],
        "clip_layers.py": [
            "import arcpy", "", 'arcpy.Clip_analysis("roads", "boundary", "clipped")'],
        "rebuild_index.py": [
            "import sys", "", "try:", "    rebuild()", "except Exception:",
            "    sys.exit(1)"],
    }
    expected = [
        "SILENT     ./mail_summary.py  handlers=1 quiet=1 exit=zero  notifies=smtplib",
        "    line 12    except Exception as exc:     QUIET",
        "SILENT     ./nightly_extract.py  handlers=1 quiet=1 exit=none  notifies=ftplib",
        "    line 8     except:                      QUIET",
        "              AddError is a print in a standalone .py, not a failure; "
        "only a toolbox turns it red",
        "ENVNOOP    ./gated_load.py  handlers=0 quiet=0 exit=zero  gates=1 env=LOAD_DEV_MODE",
        "    line 5     env gate                     NOOP env=LOAD_DEV_MODE via=DEV_MODE",
        "CLEAN      ./clip_layers.py  handlers=0 quiet=0 exit=none",
        "CLEAN      ./rebuild_index.py  handlers=1 quiet=0 exit=nonzero",
        "    line 5     except Exception:            reports",
        "ALWAYSZERO silent=2 (notifying=2) envnoop=1 reports=0 clean=2 "
        "unreadable=0 scanned=5",
        "Rewrite the handlers against a run harness that owns the exit code: "
        "https://github.com/uhsear/jobharness",
        "Move each environment switch to an explicit command line flag, so a "
        "scheduler has to type it.",
    ]
    root = tempfile.mkdtemp(prefix="alwayszero-sample-")
    try:
        for name, lines in sorted(sample.items()):
            with open(os.path.join(root, name), "w") as handle:
                handle.write("\n".join(lines) + "\n")
        code, output = run([root, "--sites"])
        shown = output.replace(root, ".").replace("\\", "/").splitlines()
        check(code == 1 and shown == expected,
              "the five-script sample in the README prints exactly that report, exit 1")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- end to end, on a temporary tree
    root = tempfile.mkdtemp(prefix="alwayszero-")
    try:
        with open(os.path.join(root, "nightly.py"), "w") as handle:
            handle.write("import ftplib\ntry:\n    pull()\nexcept:\n"
                         "    print('failed')\n")
        with open(os.path.join(root, "good.py"), "w") as handle:
            handle.write("import sys\ntry:\n    pull()\nexcept Exception:\n"
                         "    sys.exit(1)\n")
        with open(os.path.join(root, "notes.txt"), "w") as handle:
            handle.write("except:\n    pass\n")
        os.mkdir(os.path.join(root, "__pycache__"))
        with open(os.path.join(root, "__pycache__", "cached.py"), "w") as handle:
            handle.write("try:\n    go()\nexcept:\n    pass\n")
        code, output = run([root])
        check(code == 1, "a tree with a silent script exits 1")
        check("nightly.py" in output, "the silent script is named")
        check("cached.py" not in output,
              "a skipped directory is not walked")
        check("notes.txt" not in output,
              "a .txt file in the tree is not scanned")
        check("scanned=2" in output, "both .py files were scanned")
        check("    line 4" not in output, "sites are not printed by default")
        code, output = run([root, "--sites"])
        check("    line 4" in output, "--sites prints the handler line")
        code, output = run([root, "--json"])
        payload = json.loads(output)
        check(payload["summary"]["silent"] == 1,
              "--json reports one silent file")
        check(payload["files"][0]["verdict"] == SILENT,
              "--json puts the worst verdict first")
        check(payload["files"][0]["sites"][0]["quiet"] is True,
              "--json carries the per-handler detail")
        code, output = run([os.path.join(root, "good.py")])
        check(code == 0, "a file that can exit non-zero exits 0")
        check("CLEAN" in output, "that file is reported CLEAN")
        with open(os.path.join(root, "broken.py"), "w") as handle:
            handle.write('x = "unterminated\n')
        code, output = run([os.path.join(root, "broken.py")])
        check(code == 2, "an unreadable file alone exits 2")
        code, output = run([root])
        check(code == 1, "a silent script outranks an unreadable one")
        code, output = run([os.path.join(root, "missing.py")])
        check(code == 2, "a path that does not exist exits 2, never 0")
        # Years of Windows editors. A file this cannot decode is a
        # file it would skip, and a scanner that skips its subject is the
        # control that can never report red.
        legacy = os.path.join(root, "cp1252.py")
        with open(legacy, "wb") as handle:
            handle.write(b"# caf\xe9 update\ntry:\n    go()\nexcept:\n    pass\n")
        check(scan_path(legacy).verdict == SILENT,
              "a cp1252 comment does not stop the scan  <-- pinned defect")
        utf8 = os.path.join(root, "utf8.py")
        with open(utf8, "wb") as handle:
            handle.write(b"# caf\xc3\xa9\n")
        check(read_source(utf8) == "# caf\u00e9\n",
              "a UTF-8 file is decoded as UTF-8, not as cp1252")
        undecodable = os.path.join(root, "bytes.py")
        with open(undecodable, "wb") as handle:
            handle.write(b"# \x81\x90\ntry:\n    go()\nexcept:\n    pass\n")
        check(scan_path(undecodable).verdict == SILENT,
              "a byte no codec claims does not stop the scan either")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- an environment gate, for real
    # The claim is that an inherited variable turns a run into an exit-0 no-op.
    # Run the fixture in a child process, which inherits this process's
    # environment the way a scheduled task inherits its account's.
    root = tempfile.mkdtemp(prefix="alwayszero-env-")
    try:
        fixture = os.path.join(root, "gated.py")
        marker_file = os.path.join(root, "ran.txt")
        with open(fixture, "w") as handle:
            handle.write("import os\nimport sys\n"
                         "if os.environ.get('ALWAYSZERO_RUN_MODE'):\n"
                         "    sys.exit(0)\n"
                         "with open(sys.argv[1], 'w') as out:\n"
                         "    out.write('ran')\n")
        clean_env = dict(os.environ)
        clean_env.pop("ALWAYSZERO_RUN_MODE", None)
        inherited = dict(clean_env, ALWAYSZERO_RUN_MODE="1")
        done = subprocess.call([sys.executable, fixture, marker_file],
                               env=inherited)
        check(done == 0 and not os.path.exists(marker_file),
              "with the variable inherited the fixture exits 0 and does nothing")
        done = subprocess.call([sys.executable, fixture, marker_file],
                               env=clean_env)
        check(done == 0 and os.path.exists(marker_file),
              "without the variable the same fixture does its work")
        check(subprocess.call([sys.executable, "-c",
                               "import sys; sys.exit(None)"],
                              env=clean_env) == 0,
              "sys.exit(None) ends the process with status 0")

        def status_of(code):
            with open(os.devnull, "w") as sink:
                return subprocess.call([sys.executable, "-c", code],
                                       env=clean_env, stderr=sink)

        check(status_of("import sys; sys.exit('message')") == 1,
              "sys.exit with a message ends the process with status 1")
        check(status_of("import sys; sys.exit(False)") == 0
              and status_of("quit(3)") == 3,
              "sys.exit(False) ends with status 0 and quit(3) with status 3")
        code, output = run([fixture, "--sites"])
        check(code == 1 and output.startswith(ENVNOOP)
              and "ALWAYSZERO_RUN_MODE" in output,
              "the scanner reports that same fixture as ENVNOOP, exit 1")
        check("explicit command line flag" in output,
              "the remedy line names the command line flag")
        code, output = run([fixture, "--json"])
        payload = json.loads(output)
        check(payload["summary"]["envnoop"] == 1
              and payload["files"][0]["gates"][0]["env"]
              == ["ALWAYSZERO_RUN_MODE"],
              "--json counts the gate and names its variable")
        with open(fixture, "w") as handle:
            handle.write("import sys\nDEV = '--dev' in sys.argv\n"
                         "if DEV:\n    sys.exit(0)\nwork()\n")
        code, output = run([fixture])
        check(code == 0 and "CLEAN" in output,
              "the fix, an explicit command line flag, scans CLEAN, exit 0")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- the harness, and the module run as a script
    # The harness is the control every other line depends on, so make it fail
    # on purpose and look at what it says.
    probe = _Harness()
    code, output = capture(lambda: (
        probe.check(False, "bad check"),
        probe.raises(lambda: None, "no error"),
        probe.raises(lambda: 1 // 0, "wrong error"),
        probe.raises(lambda: int("x"), "right error"),
        probe.finish())[-1])
    check(code == 1 and probe.passed == 1 and len(probe.failed) == 3,
          "the harness counts a failed check, a missing error and a wrong one")
    check("4 assertions, 3 failed" in output and "FAILED: bad check" in output
          and "no error raised" in output and "wrong exception" in output,
          "a failing run prints its failures and the footer")
    import runpy
    root = tempfile.mkdtemp(prefix="alwayszero-main-")
    saved_argv = sys.argv
    try:
        target = os.path.join(root, "fine.py")
        with open(target, "w") as handle:
            handle.write("go()\n")
        sys.argv = [__file__, target]

        def as_script():
            try:
                runpy.run_path(__file__, run_name="__main__")
            except SystemExit as exc:
                return exc.code

        code, output = capture(as_script)
        check(code == 0 and "CLEAN" in output,
              "run as a script, the module exits with main()'s status")
        code, output = capture(lambda: runpy.run_path(
            __file__, run_name="alwayszero_imported"))
        check(output == "", "imported rather than run, the module does nothing")
    finally:
        sys.argv = saved_argv
        shutil.rmtree(root, ignore_errors=True)

    code, output = run([])
    check(code == 64, "no path at all is a usage error")
    check("--self-test" in output, "the usage error names --self-test")

    return harness.finish()


# --------------------------------------------------------------------- cli

class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad flag, which this tool uses for an unreadable
    tree. A refused flag exits 64, the code for a usage error."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, "%s: error: %s\n" % (self.prog, message))


def _parse(argv):
    ap = _Parser(
        prog="alwayszero.py",
        description="Name every scheduled script that cannot report failure.",
        epilog="Exit 1 means work was found, so this drives a detect-and-"
               "remediate loop. A tree that could not be read is never clean.",
        allow_abbrev=False,
    )
    ap.add_argument("paths", nargs="*",
                    help="files or directories to scan")
    ap.add_argument("--sites", action="store_true",
                    help="print every catch-all handler, not one line per file")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output for a dashboard")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.self_test:
        return self_test()

    if not args.paths:
        print("error: give at least one file or directory. Use --self-test to "
              "verify the tool without a corpus.", file=sys.stderr)
        return 64

    reports = scan_paths(args.paths)
    silent = [r for r in reports if r.verdict == SILENT]
    gated = [r for r in reports if r.verdict == ENVNOOP]
    unreadable = [r for r in reports if r.verdict == UNREADABLE]

    if args.json:
        counts = {"silent": len(silent), "envnoop": len(gated),
                  "unreadable": len(unreadable),
                  "reports": len([r for r in reports if r.verdict == REPORTS]),
                  "clean": len([r for r in reports if r.verdict == CLEAN]),
                  "scanned": len(reports)}
        print(json.dumps({"summary": counts,
                          "files": [r.as_dict() for r in reports]}, indent=2))
    else:
        for report in reports:
            print(printable(report_line(report)))
            if args.sites:
                for line in site_lines(report):
                    print(printable(line))
        print(summary(reports))
        if silent:
            # The remedy, named once. jobharness is the wrapper these handlers
            # should have been written against in the first place.
            print("Rewrite the handlers against a run harness that owns the "
                  "exit code: https://github.com/uhsear/jobharness")
        if gated:
            print("Move each environment switch to an explicit command line "
                  "flag, so a scheduler has to type it.")

    if silent or gated:
        return 1
    if unreadable:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
