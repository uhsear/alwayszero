#!/usr/bin/env python
"""Name every scheduled script that cannot report failure, on a tree a parser cannot read.

Task Scheduler says Last Run Result 0x0. It has said that every night for four
years. The script wraps its work in a bare except, prints a sentence into a
window nobody watches, and falls off the end of the file, so the process exits 0
whether the FTP connection opened or not:

    try:
        ftp.retrbinary("RETR export.zip", handle.write)
        arcpy.TruncateTable_management(target)
        arcpy.Append_management(staged, target, "NO_TEST")
    except:
        print arcpy.GetMessages(2)          # a print. The process still exits 0.

This reads a file with tokenize, masks its comments and strings, walks each
handler body by indentation, and forms one verdict for the whole file: a
catch-all handler that neither re-raises nor exits, and no non-zero exit
anywhere else in the file, means the exit code is a constant. When the same file
also imports smtplib or ftplib it mails or uploads on a schedule and still
cannot say the word failed, which is the worst shape here and is ranked first
among the SILENT files.

ruff and pylint own the single handler and own it well. E722 flags a bare
except, BLE001 a blind except Exception, S110 a try/except/pass, and W0702 and
W0703 do the same for pylint. Run them. Two things are left over. They are
Python 3 parsers, and many old scheduled scripts are Python 2, which a Python 3
parser rejects whole: ruff then reports syntax errors and no handler at all,
while tokenize still reads nearly every such file. And none of them forms a
whole-file verdict: a handler is flagged, but no tool says this file cannot exit
non-zero and it emails somebody about it. pytlint gained a script mode and rule
PYT023 for it, and it is the better tool on a file that parses, but it needs an
ast and an `if __name__` guard, which few old scripts have. This tool is for the
other tree. Nothing here is a reason to skip ruff on code that parses. The
counts from the private tree this was built on are not published.

    python alwayszero.py --self-test
    python alwayszero.py nightly_job.py
    python alwayszero.py D:/GIS/Scripts --sites
    python alwayszero.py D:/GIS/Scripts --json

The second shape is the environment gate. A dev switch read from the environment
-- `if os.environ.get("RUN_MODE"): sys.exit(0)` -- is inherited by every child
process, so one variable set once on a workstation, in a profile or in a service
account turns the next nightly run into a job that exits 0 having done nothing.
The verdict for such a file is ENVNOOP. It means "an environment-conditional
exit 0", not "does nothing": the block may do real work first. A switch belongs
on the command line, where a scheduler has to type it on purpose.

Exit codes: 0 no silent script and no environment gate, 1 at least one of
either, 2 nothing found but a file or directory could not be read, or no .py or
.pyw file was found to scan, 64 usage error (a bad flag included).

EXIT CODE POLARITY. 1 means work was found, so this drives a detect-and-
remediate loop the same way stalehost does. A tree that could not be read is
never reported clean, and neither is a tree with nothing in it to scan: that is
what exit 2 is for.
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

# Process exit codes. 1 means work was found.
EXIT_FOUND = 1
EXIT_UNREAD = 2
EXIT_USAGE = 64


class UnreadableSource(ValueError):
    """tokenize could not read the source. Never treat this as clean."""


# --------------------------------------------------------------- the scanner

# One handler clause. The body is everything indented under it, which is why
# this is a line scanner and not a parser: many of the old files this was built
# for do not parse as Python 3, so there is no ast to walk.
_EXCEPT_RE = re.compile(r"^([ \t]*)except\b([^:]*):(.*)$")

# The opening of sys.exit(, os._exit(, the site builtins exit( and quit(, and
# SystemExit(. The argument is read by exit_status, which balances the brackets,
# so sys.exit(main()) has its argument. The lookbehinds keep the bare-name
# branch off the tail of sys.exit and off `def exit(self)` and `class SystemExit(`.
_EXIT_RE = re.compile(
    r"(?<![\w.])(?<!def )(?<!class )"
    r"(?:sys\.exit|os\._exit|exit|quit|SystemExit)\s*\(")

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

# An if or elif header, and an assignment. `==` is not an assignment.
_IF_RE = re.compile(r"^([ \t]*)(?:if|elif)\b(.*)$")
_ASSIGN_RE = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*=(?!=)(.*)$")

# Arguments that mean the process ends successfully anyway. "0L" is a Python 2
# long literal and turns up in this corpus. Anything else is a failure,
# including a string: sys.exit("message") prints the message and exits 1.
_ZERO_STATUS = ("", "0", "0L", "None", "False", "0.0", "0x0", "0o0", "0b0")


def _strip_bom(text):
    """Drop a leading UTF-8 byte order mark, the way Python does on import."""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _lines(text):
    """Split source into lines the way tokenize counts them.

    str.splitlines also breaks on a form feed, U+2028, U+0085 and a lone CR,
    which tokenize does not, so every line number after one would shift. A form
    feed is kept as a space, because Python only uses it to reset a column and
    the width must not change. A trailing CR of a CRLF pair is dropped.
    """
    return [line[:-1] if line.endswith("\r") else line
            for line in text.replace("\x0c", " ").split("\n")]


def mask_source(text, tokenizer=tokenize.generate_tokens):
    """Return the source as lines with every comment and string body blanked.

    Blanking preserves line and column positions, so a finding still reports
    the line the reader will open. The point is that the word raise inside a
    docstring, and an except: inside a SQL string, stop being code.

    Raises UnreadableSource when tokenize refuses the file. Python 3.9 and 3.10
    do not raise on an unterminated one-line string: they yield the opening
    quote as an ERRORTOKEN and carry on, so that token is refused here too.
    The tokenizer argument is for the self-test, which feeds it that stream.
    """
    # A Windows editor saves a BOM; Python strips it on import, tokenize
    # does not, and the first line would otherwise look like garbage.
    text = _strip_bom(text)
    # Python 3.12 refuses a NUL byte. 3.9 to 3.11 read on through it, so the
    # verdict would depend on the interpreter. Refuse it everywhere.
    if "\x00" in text:
        raise UnreadableSource("source contains NUL bytes")
    grid = [list(line) for line in _lines(text)]
    try:
        tokens = list(tokenizer(io.StringIO(text).readline))
    except Exception as exc:
        # Deliberately broad. A file tokenize chokes on in any way is a finding
        # (UNREADABLE), and one pathological file must not abort the scan of
        # the rest of the tree.
        raise UnreadableSource(str(exc) or exc.__class__.__name__)
    for token in tokens:
        if token[0] == tokenize.ERRORTOKEN and token[1] in ("'", '"'):
            raise UnreadableSource("unterminated string literal on line %d"
                                   % token[2][0])
    for token in tokens:
        if token[0] not in (tokenize.STRING, tokenize.COMMENT):
            continue
        (srow, scol), (erow, ecol) = token[2], token[3]
        # min() rather than a bounds check: a guard here would be a branch no
        # input can reach, since _lines counts lines the way tokenize does.
        for row in range(srow, min(erow, len(grid)) + 1):
            line = grid[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            for col in range(start, min(end, len(line))):
                line[col] = " "
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


def exit_status(text, original=None):
    """Whether this text ends the process, and with what status.

    Returns "nonzero" when any exit call carries a status that is not zero,
    "zero" when every exit call is an exit(0), and "none" when there is none.
    A file whose only exit is sys.exit(0) is the same lie as a file with no
    exit at all, and the report says which one it was.

    text is masked source. original is the same text unmasked, character for
    character, and is read only to tell sys.exit("message"), which exits 1,
    from sys.exit(), because the masker blanks the string to spaces.
    """
    if original is None:
        original = text
    status = NO_EXIT
    for match in _EXIT_RE.finditer(text):
        start = end = match.end()
        depth = 1
        # Balance the brackets, so sys.exit(main()) has its whole argument.
        # A call left open at the end of the text takes the rest of it.
        while end < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        stop = end - 1 if depth == 0 else end
        argument = text[start:stop].strip()
        quoted = '"' in original[start:stop] or "'" in original[start:stop]
        if not argument and quoted:
            return NONZERO_EXIT      # a blanked string literal
        if argument in _ZERO_STATUS:
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


def block_after(masked, original, index, indent, tail):
    """The block a header line opens, as (masked text, original text).

    The text is the header's tail, then the deeper lines. A blank line does not
    end the block. The first non-blank line at or above the header's own indent
    does. The two texts line up character for character, because the masker
    keeps every position.
    """
    own = _indent(indent + "x")
    end = index + 1
    for following in masked[index + 1:]:
        if following.strip() and _indent(following) <= own:
            break
        end += 1
    # tail is a suffix of the masked header line, so it has an original twin.
    twin = original[index][len(masked[index]) - len(tail):]
    return ("\n".join([tail] + masked[index + 1:end]),
            "\n".join([twin] + original[index + 1:end]))


def find_handlers(masked, original):
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
        text, source = block_after(masked, original, index, indent, tail)
        reraises = bool(_RAISE_RE.search(text))
        exits = exit_status(text, source) == NONZERO_EXIT
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
    None means the header carries on to the next line, which a line scanner
    does not follow.
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


def find_gates(masked, original):
    """Every if or elif whose condition reads the environment and whose block
    ends the process with status zero.

    A name assigned from an environment read carries it to a later condition,
    until the name is assigned something else. A block that can also exit
    non-zero fails loudly and is not a gate. `original` is the unmasked lines,
    for the variable names the masker blanked.
    """
    carried = {}      # name -> env names from the assignment that set it
    gates = []
    for index, line in enumerate(masked):
        match = _ASSIGN_RE.match(line)
        if match is not None:
            name = match.group(1)
            if _ENV_RE.search(match.group(2)):
                carried[name] = env_names(original[index])
            else:
                carried.pop(name, None)
            continue
        match = _IF_RE.match(line)
        if match is None:
            continue
        parts = split_header(match.group(2))
        if parts is None:
            continue
        condition, tail = parts
        direct = _ENV_RE.search(condition) is not None
        via = sorted(name for name in carried
                     if re.search(r"(?<![\w.])%s\b" % re.escape(name), condition))
        if not direct and not via:
            continue
        if exit_status(*block_after(masked, original, index, match.group(1),
                                    tail)) != ZERO_EXIT:
            continue
        names = set(env_names(original[index])) if direct else set()
        for name in via:
            names.update(carried[name])
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


def scan_source(text, name="<source>"):
    """The verdict for one source. Pure: no file, no network, no arcpy.

    Three conditions decide it, and they are deliberately whole-file. A script
    that can exit non-zero anywhere is trusted to do it, because a line scanner
    cannot tell which try block a given raise sits inside, and guessing wrong
    in the other direction reports a job that does work.
    """
    try:
        masked = mask_source(text)
    except UnreadableSource as exc:
        return Report(name, UNREADABLE, reason=str(exc))
    # ponytail: the exit test is whole-file, with no idea which try block a
    # given exit sits inside, so a sys.exit(1) the surrounding bare except
    # would swallow still clears the file. Upgrade path: when the source
    # parses, hand it to pytlint PYT023, which walks an ast and knows.
    original = _lines(_strip_bom(text))
    sites = find_handlers(masked, original)
    exits = exit_status("\n".join(masked), "\n".join(original))
    notifies = notifiers(masked)
    gates = find_gates(masked, original)
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
    an UNREADABLE report, and never a silent skip: os.walk ignores listing
    errors unless it is given onerror. The walk argument is for the self-test.
    """
    found = []
    failures = []

    def on_error(exc):
        failures.append(Report(str(exc.filename), UNREADABLE, reason=str(exc)))

    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in walk(path, onerror=on_error):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRECTORIES)
                for name in sorted(files):
                    if name.lower().endswith(SOURCE_EXTENSIONS):
                        found.append(os.path.join(root, name))
        else:
            found.append(path)
    return found, failures


def read_source(path):
    """Decode a file the way a scanner must: never fail on an old encoding.

    These files were written on Windows over fifteen years. cp1252 bytes in a
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
    import shutil
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
    check(exit_status("sys.exit (1)") == NONZERO_EXIT,
          "white space before the opening bracket of an exit call is read")
    check(exit_status("def exit(self):\n    return 1\n") == NO_EXIT
          and exit_status("class SystemExit(Exception):\n    pass\n") == NO_EXIT,
          "defining a function or class named exit is not a process exit  <-- pinned defect")
    check(exit_status("quit(0)") == ZERO_EXIT
          and exit_status("quit(2)") == NONZERO_EXIT,
          "the site builtin quit ends the process like exit")
    check(exit_status("sys.exit(False)") == ZERO_EXIT
          and exit_status("sys.exit(0x0)") == ZERO_EXIT
          and exit_status("sys.exit(0.0)") == ZERO_EXIT,
          "False, 0x0 and 0.0 are zero statuses  <-- pinned defect")
    check(exit_status("sys.exit(0") == ZERO_EXIT,
          "a call left open at the end of the text still yields its argument")

    # ---- the argument of an exit is read whole, and a string is a failure
    # sys.exit(main()) is the usual entry point. A pattern that forbade a
    # bracket inside the argument saw no exit at all, so a script that could
    # fail was called silent.
    check(exit_status("sys.exit(main())") == NONZERO_EXIT
          and exit_status("sys.exit(len(errors))") == NONZERO_EXIT
          and exit_status("sys.exit(code_for(exc, (1, 2)))") == NONZERO_EXIT,
          "an exit whose argument is a call is an exit, and not a zero one  <-- pinned defect")
    check(verdict("import sys\ndef main():\n    try:\n        go()\n"
                  "    except Exception:\n        return 1\n    return 0\n"
                  "sys.exit(main())\n") == REPORTS,
          "a script that ends with sys.exit(main()) is not silent  <-- pinned defect")
    # The masker blanks a string to spaces, so sys.exit("message") used to read
    # as sys.exit(), which exits 0. Python prints the message and exits 1.
    check(verdict("import sys\ntry:\n    go()\nexcept Exception:\n"
                  "    print('failed')\n    sys.exit('load failed')\n") == CLEAN,
          "a handler that ends with sys.exit('message') exits 1 and is not silent  <-- pinned defect")
    check(verdict("import sys\ntry:\n    go()\nexcept Exception:\n"
                  "    print('failed')\n    sys.exit('')\n") == CLEAN,
          "sys.exit('') with an empty string also exits 1")
    check(verdict("if os.environ.get('K'):\n    sys.exit('K is set, refusing')\n"
                  ) == CLEAN,
          "a gate that ends with sys.exit('message') exits 1 and is no no-op  <-- pinned defect")
    check(verdict("if os.environ.get('K'):\n    sys.exit(\n        # why\n    )\n"
                  ) == ENVNOOP,
          "a comment inside the exit brackets is not a string")

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
    check(scan_source("import arcpy\ntry:\n    go()\nexcept:\n"
                      "    arcpy.AddReturnMessage(1)\n").sites[0].note != "",
          "AddReturnMessage carries the same note as AddError")

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

    # ---- python 2, which a python 3 parser rejects whole
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

    # ---- lines are counted the way tokenize counts them
    # str.splitlines also breaks on these, so every line number after one of
    # them was one too high, and where the blanking landed moved with it.
    for label, char in (("a form feed", "\x0c"), ("U+2028", " "),
                        ("U+0085", "\x85"), ("a lone CR", "\r")):
        check(scan_source("# a%sb\ntry:\n    go()\nexcept:\n    pass\n"
                          % char).sites[0].line == 4,
              "%s inside a comment does not shift the line numbers  <-- pinned defect"
              % label)
        check(scan_source("s = 'a%sb'\ntry:\n    go()\nexcept:\n    pass\n"
                          % char).sites[0].line == 4,
              "%s inside a string does not shift the line numbers  <-- pinned defect"
              % label)
    source = ("try:\n    go()\nexcept:\n    s = '''\n    raise\n    '''\n")
    check(verdict(source) == SILENT
          and verdict(source.replace("\n", "\n\x0c")) == SILENT,
          "a form feed after every line does not change the verdict  <-- pinned defect")
    report = scan_source("try:\r\n    go()\r\nexcept:\r\n    print('x')\r\n")
    check(report.verdict == SILENT and report.sites[0].line == 3,
          "a CRLF file is read, and its handler is on the line the reader opens")
    check(_indent("\tx") == 8 and _indent("  \tx") == 8
          and _indent("\t  x") == 10,
          "a tab advances the indent to the next multiple of eight")

    # ---- unreadable is never clean
    check(verdict("x = 1\x00\ntry:\n    go()\nexcept:\n    pass\n") == UNREADABLE,
          "a NUL byte is UNREADABLE on every python, not judged from garbage  <-- pinned defect")

    def broken_tokenizer(readline):
        raise SystemError("tokenizer exploded")

    def mute_tokenizer(readline):
        raise ValueError()

    def reason(tokenizer):
        try:
            mask_source("x = 1\n", tokenizer)
        except UnreadableSource as exc:
            return str(exc)
        return None

    check(reason(broken_tokenizer) == "tokenizer exploded",
          "any error out of tokenize makes the file UNREADABLE, not a crash  <-- pinned defect")
    check(reason(mute_tokenizer) == "ValueError",
          "an error with no message is named by its class")
    raises(lambda: mask_source('x = "unterminated\n'),
           "an unterminated string raises UnreadableSource")
    # Python 3.9 and 3.10 yield the opening quote as an ERRORTOKEN instead of
    # raising. Feed that stream in so every host takes the branch.
    def quote_stream(readline):
        yield (tokenize.ERRORTOKEN, '"', (1, 4), (1, 5), 'x = "oops\n')
    raises(lambda: mask_source('x = "oops\n', quote_stream),
           "an ERRORTOKEN quote, as python 3.9 yields it, is unreadable  <-- pinned defect")
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
    check(summary([clean]) == "ALWAYSZERO silent=0 (notifying=0) envnoop=0 "
          "reports=0 clean=1 unreadable=0 scanned=1"
          and summary([]) == "ALWAYSZERO silent=0 (notifying=0) envnoop=0 "
          "reports=0 clean=0 unreadable=0 scanned=0",
          "an absent verdict counts as zero in the summary")
    broken = scan_source('x = "oops\n', "zz_broken.py")
    checked = scan_source("try:\n go()\nexcept:\n log()\nexit(1)\n", "r.py")
    check(summary([clean, quiet_plain, checked, broken])
          == "ALWAYSZERO silent=1 (notifying=0) envnoop=0 reports=1 clean=1 "
          "unreadable=1 scanned=4",
          "the summary counts every verdict once")
    # Two silent files, the same notification state. Name order alone would put
    # a.py first, so only the quiet-handler count can put b.py ahead.
    one = scan_source("try:\n go()\nexcept:\n log()\n", "a.py")
    two = scan_source("try:\n go()\nexcept:\n log()\ntry:\n go()\nexcept:\n"
                      " log()\n", "b.py")
    check([r.name for r in sorted([one, two], key=rank)] == ["b.py", "a.py"],
          "more quiet handlers rank higher among files that are otherwise equal")
    check([r.name for r in sorted([quiet_plain, broken], key=rank)]
          == ["zz_broken.py", "plain.py"],
          "an unreadable file is ranked ahead of every verdict")

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
    check(scan_source("if (os.environ.get('K') and\n        other):\n"
                      "    exit(0)\n").verdict == CLEAN,
          "a condition split across lines is not read  <-- known limit")
    check(scan_source("os.environ.get('K') and sys.exit(0)\n").verdict == CLEAN,
          "a gate written as one expression is not read  <-- known limit")
    check(scan_source("if os.environ.get('K'):\n    raise SystemExit\n"
                      ).verdict == CLEAN,
          "a bare raise SystemExit is not counted as an exit  <-- known limit")
    check(scan_source("t = os.environ.get('K')\nf(\n    t=1,\n)\nif t:\n"
                      "    exit(0)\n").verdict == CLEAN,
          "a keyword argument line is read as an assignment  <-- known limit")
    check(scan_source("if os.environ.get('K'):\n    quit(0)\n").verdict
          == ENVNOOP, "quit(0) ends the run like exit(0)")
    check(scan_source("if os.environ.get('K'):\n    work()\n    sys.exit(0)\n"
                      ).verdict == ENVNOOP,
          "a block that does real work and then exits 0 is still ENVNOOP  <-- known limit")
    check(scan_source("if os.environ.get('RUN'):\n    work()\nelse:\n"
                      "    sys.exit(0)\n").verdict == CLEAN,
          "an exit 0 in the else of an environment test is not read  <-- known limit")
    check(scan_source("def main():\n    if os.environ.get('K'):\n"
                      "        return\n    work()\n").verdict == CLEAN,
          "a bare return after an environment test is not an exit  <-- known limit")
    check(scan_source("import os as _os\nif _os.environ.get('K'):\n"
                      "    exit(0)\n").verdict == CLEAN
          and scan_source("from os import getenv as ge\nif ge('K'):\n"
                          "    exit(0)\n").verdict == CLEAN,
          "an aliased os or getenv is not followed  <-- known limit")
    check(scan_source("DEV: bool = os.environ.get('K')\nif DEV:\n    exit(0)\n"
                      ).verdict == CLEAN
          and scan_source("A, B = os.environ.get('K'), 1\nif A:\n    exit(0)\n"
                          ).verdict == CLEAN,
          "an annotated or tuple assignment is not followed  <-- known limit")
    check(scan_source("def is_dev():\n    return os.environ.get('K')\n"
                      "if is_dev():\n    exit(0)\n").verdict == CLEAN,
          "an environment read hidden in a helper function is not followed  <-- known limit")
    gate = scan_source("env = os.environ\nif env.get('K'):\n    exit(0)\n")
    check(gate.verdict == ENVNOOP and gate.gate_env == [],
          "an alias of os.environ is flagged, but the variable name is not recovered")
    check(scan_source("try:\n    go()\nexcept:\n    if DEBUG:\n        raise\n"
                      "    log()\n").verdict == CLEAN,
          "a raise under an if still counts as a re-raise  <-- known limit")

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

    def refusal(argv):
        """The exit code the parser exits with on argv, or None if it accepts."""
        saved = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _parse(argv)
        except SystemExit as exc:
            return exc.code
        finally:
            sys.stderr = saved
        return None

    def refused(argv):
        return refusal(argv) is not None

    check(refusal(["--bogus"]) == 64 and refusal(["a.py", "--ap"]) == 64,
          "a bad flag exits 64, not argparse's 2, which means an unreadable file  <-- pinned defect")
    check(capture(lambda: refusal(["--help"]))[0] == 0,
          "--help still exits 0")

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
        check("jobharness" in output
              and "explicit command line flag" not in output,
              "a silent script gets the handler remedy and no gate remedy")
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
        # Fifteen years of Windows editors. A file this cannot decode is a
        # file it would skip, and a scanner that skips its subject is the
        # control that can never report red.
        legacy = os.path.join(root, "cp1252.py")
        with open(legacy, "wb") as handle:
            handle.write(b"# caf\xe9 update\ntry:\n    go()\nexcept:\n    pass\n")
        check(scan_path(legacy).verdict == SILENT,
              "a cp1252 comment does not stop the scan  <-- pinned defect")
        undecodable = os.path.join(root, "bytes.py")
        with open(undecodable, "wb") as handle:
            handle.write(b"# \x81\x90\ntry:\n    go()\nexcept:\n    pass\n")
        check(scan_path(undecodable).verdict == SILENT,
              "a byte no codec claims does not stop the scan either")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- file types, the JSON summary, and a tree with nothing to scan
    root = tempfile.mkdtemp(prefix="alwayszero-kinds-")
    try:
        quiet_text = "try:\n    go()\nexcept:\n    log()\n"
        for name, text in (("task.pyw", quiet_text), ("LOUD.PY", quiet_text),
                           ("rep.py", quiet_text + "exit(1)\n"),
                           ("ok.py", "go()\n")):
            with open(os.path.join(root, name), "w") as handle:
                handle.write(text)
        code, output = run([root, "--json"])
        payload = json.loads(output)
        check(payload["summary"] == {"silent": 2, "envnoop": 0,
                                     "unreadable": 0, "reports": 1,
                                     "clean": 1, "scanned": 4},
              "--json counts every verdict exactly")
        names = [os.path.basename(item["file"]) for item in payload["files"]]
        check("task.pyw" in names, "a .pyw file is scanned")
        check("LOUD.PY" in names, "an upper-case extension is scanned")
        check(code == 1, "two silent scripts exit 1")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root = tempfile.mkdtemp(prefix="alwayszero-empty-")
    try:
        code, output = run([root])
        check(code == 2 and "scanned=0" in output
              and "nothing was assessed" in output,
              "an empty directory exits 2 and says nothing was assessed  <-- pinned defect")
        with open(os.path.join(root, "notes.txt"), "w") as handle:
            handle.write("except:\n    pass\n")
        code, output = run([root, "--json"])
        check(code == 2 and "nothing was assessed" in output,
              "a directory with no .py or .pyw file also exits 2, with --json too")
        code, output = run([os.path.join(root, "notes.txt")])
        check(code == 0 and "CLEAN" in output,
              "a file named on the command line is scanned whatever its extension")

        # A directory os.walk cannot list is reported to onerror and then
        # skipped. A real unreadable directory is made by hand on both hosts
        # (see the README); here the walk is made to fail the same way.
        real_walk = os.walk

        def flaky_walk(path, onerror=None):
            for item in real_walk(path, onerror=onerror):
                yield item
            onerror(OSError(13, "Permission denied",
                            os.path.join(path, "locked")))

        os.walk = flaky_walk
        try:
            code, output = run([root])
        finally:
            os.walk = real_walk
        check(code == 2 and "UNREADABLE" in output
              and "locked" in output and "Permission denied" in output,
              "a directory that cannot be listed is UNREADABLE and exits 2  <-- pinned defect")
        check("scanned=1" in output and "nothing was assessed" not in output,
              "the unlistable directory counts as scanned")
        with open(os.path.join(root, "nightly.py"), "w") as handle:
            handle.write(quiet_text)
        os.walk = flaky_walk
        try:
            code, output = run([root])
        finally:
            os.walk = real_walk
        check(code == 1 and "UNREADABLE" in output and "SILENT" in output,
              "a silent script is still named when another directory could not be read")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # A file name the console cannot encode must not crash the report. The
    # stream here is ASCII, the way cp1252 is for a Greek letter on Windows.
    root = tempfile.mkdtemp(prefix="alwayszero-name-")
    try:
        with open(os.path.join(root, "café.py"), "w") as handle:
            handle.write("go()\n")
        raw = io.BytesIO()
        narrow = io.TextIOWrapper(raw, encoding="ascii")
        saved = sys.stdout
        sys.stdout = narrow
        try:
            code = main([root])
            narrow.flush()
        finally:
            sys.stdout = saved
        check(code == 0 and b"caf\\xe9.py" in raw.getvalue(),
              "a file name the output encoding cannot hold is escaped, not a crash  <-- pinned defect")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- an environment gate, for real
    # The claim is that an inherited variable turns a run into an exit-0 no-op.
    # Run the fixture in a child process, which inherits this process's
    # environment the way a scheduled task inherits its account's.
    import subprocess
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
    """argparse exits 2 on a bad flag, and 2 here means a file was unreadable."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, "%s: error: %s\n" % (self.prog, message))


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
        return EXIT_USAGE

    # A report line holds file names, and a name the console's code page cannot
    # encode would crash print with exit 1, which reads as "work found".
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    sources, failures = iter_sources(args.paths)
    reports = sorted([scan_path(path) for path in sources] + failures,
                     key=rank)
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
            print(report_line(report))
            if args.sites:
                for line in site_lines(report):
                    print(line)
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
        return EXIT_FOUND
    if not reports:
        # Nothing was examined, which is not the same as nothing being wrong.
        print("error: no .py or .pyw file was found under the given paths, so "
              "nothing was assessed.", file=sys.stderr)
    if unreadable or not reports:
        return EXIT_UNREAD
    return 0


if __name__ == "__main__":
    sys.exit(main())
