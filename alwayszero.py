#!/usr/bin/env python
"""Name every scheduled script that cannot report failure, on a tree a parser cannot read.

Task Scheduler says Last Run Result 0x0. It has said that every night for four
years. The script wraps its work in a bare except, prints a sentence into a
window nobody watches, and falls off the end of the file, so the process exits 0
whether the FTP connection opened or not:

    try:
        ftp.retrbinary("RETR roll.zip", handle.write)
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
Python 3 parsers, and on the 743-file county tree this was built from, 340 files
do not parse as Python 3 at all, so ruff reports a million syntax diagnostics
there and no handler at all, while tokenize reads 738 of the 743. And none of
them forms a whole-file verdict: a handler is flagged, but no tool says this
file cannot exit non-zero and it emails somebody about it. pytlint gained a
script mode and rule PYT023 for it, and it is the better tool on a file that
parses, but it needs an ast and an `if __name__` guard, which together reach 10
of those 743 files. This tool is for the other tree. Nothing here is a reason to
skip ruff on code that parses.

    python alwayszero.py --self-test
    python alwayszero.py nightly_job.py
    python alwayszero.py D:/GIS/Scripts --sites
    python alwayszero.py D:/GIS/Scripts --json

Exit codes: 0 no silent script, 1 at least one silent script, 2 nothing silent
but a file could not be read, 64 usage error.

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

VERDICT_ORDER = (UNREADABLE, SILENT, REPORTS, CLEAN)

# Exit-call status, as reported per file.
NO_EXIT = "none"
ZERO_EXIT = "zero"
NONZERO_EXIT = "nonzero"


class UnreadableSource(ValueError):
    """tokenize could not read the source. Never treat this as clean."""


# --------------------------------------------------------------- the scanner

# One handler clause. The body is everything indented under it, which is why
# this is a line scanner and not a parser: 340 of the 743 files this was built
# from have no ast to walk.
_EXCEPT_RE = re.compile(r"^([ \t]*)except\b([^:]*):(.*)$")

# sys.exit, os._exit, the site builtin exit, and raise SystemExit, with the
# argument captured so exit(0) can be told from exit(1). The lookbehind keeps
# the bare-name branch off the tail of sys.exit.
_EXIT_RE = re.compile(
    r"(?<![\w.])(?:sys\.exit|os\._exit|exit|SystemExit)\s*\(([^()]*)\)")

# raise, as a statement and not as part of an identifier such as raised_error.
_RAISE_RE = re.compile(r"(?<![\w.])raise(?![\w])")

# arcpy.AddError is the cruel shape: in a toolbox it colours the Pro window red,
# in a standalone script it is a print, and the process still exits 0.
_ADDERROR_RE = re.compile(r"(?<![\w])(?:AddError|AddIDMessage|AddReturnMessage)\s*\(")

_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+(.+)$")
_FROM_RE = re.compile(r"^[ \t]*from[ \t]+([\w.]+)[ \t]+import\b")

# Arguments that mean the process ends successfully anyway. "0L" is a Python 2
# long literal and turns up in this corpus.
_ZERO_STATUS = ("", "0", "0L", "None")


def mask_source(text):
    """Return the source as lines with every comment and string body blanked.

    Blanking preserves line and column positions, so a finding still reports
    the line the reader will open. The point is that the word raise inside a
    docstring, and an except: inside a SQL string, stop being code.

    Raises UnreadableSource when tokenize refuses the file.
    """
    if text.startswith("\ufeff"):
        # A Windows editor saves a BOM; Python strips it on import, tokenize
        # does not, and the first line would otherwise look like garbage.
        text = text[1:]
    grid = [list(line) for line in text.splitlines()]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise UnreadableSource(str(exc))
    for token in tokens:
        if token[0] not in (tokenize.STRING, tokenize.COMMENT):
            continue
        (srow, scol), (erow, ecol) = token[2], token[3]
        # min() rather than a bounds check: str.splitlines breaks on more
        # characters than tokenize counts as line ends, so the grid is never
        # short, and a guard here would be a branch no input can reach.
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


def exit_status(text):
    """Whether this text ends the process, and with what status.

    Returns "nonzero" when any exit call carries a status that is not zero,
    "zero" when every exit call is an exit(0), and "none" when there is none.
    A file whose only exit is sys.exit(0) is the same lie as a file with no
    exit at all, and the report says which one it was.
    """
    status = NO_EXIT
    for match in _EXIT_RE.finditer(text):
        if match.group(1).strip() in _ZERO_STATUS:
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
        body = [tail]
        own = _indent(indent + "x")
        for following in masked[index + 1:]:
            if not following.strip():
                continue
            if _indent(following) <= own:
                break
            body.append(following)
        text = "\n".join(body)
        reraises = bool(_RAISE_RE.search(text))
        exits = exit_status(text) == NONZERO_EXIT
        note = ""
        if not reraises and not exits and _ADDERROR_RE.search(text):
            note = ("AddError is a print in a standalone .py, not a failure; "
                    "only a toolbox turns it red")
        sites.append(Site(index + 1, " ".join(clause.split()),
                          not (reraises or exits), note))
    return sites


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
                 reason=""):
        self.name = name
        self.verdict = verdict
        self.sites = list(sites)
        self.exits = exits
        self.notifies = list(notifies)
        self.reason = reason

    @property
    def quiet_sites(self):
        return [site for site in self.sites if site.quiet]

    def as_dict(self):
        return {"file": self.name, "verdict": self.verdict,
                "exits": self.exits, "notifies": self.notifies,
                "handlers": len(self.sites),
                "quiet": len(self.quiet_sites),
                "reason": self.reason,
                "sites": [site.as_dict() for site in self.sites]}


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
    sites = find_handlers(masked)
    exits = exit_status("\n".join(masked))
    notifies = notifiers(masked)
    quiet = [site for site in sites if site.quiet]
    if not quiet:
        verdict = CLEAN
    elif exits == NONZERO_EXIT:
        verdict = REPORTS
    else:
        verdict = SILENT
    return Report(name, verdict, sites, exits, notifies)


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
    return out


def summary(reports):
    """The one line that goes in a ticket."""
    counts = {}
    for report in reports:
        counts[report.verdict] = counts.get(report.verdict, 0) + 1
    notifying = len([r for r in reports
                     if r.verdict == SILENT and r.notifies])
    return ("ALWAYSZERO silent=%d (notifying=%d) reports=%d clean=%d "
            "unreadable=%d scanned=%d" % (
                counts.get(SILENT, 0), notifying, counts.get(REPORTS, 0),
                counts.get(CLEAN, 0), counts.get(UNREADABLE, 0), len(reports)))


# --------------------------------------------------------------------- i/o

def iter_sources(paths):
    """Every source file under the given files and directories, sorted."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRECTORIES)
                for name in sorted(files):
                    if name.lower().endswith(SOURCE_EXTENSIONS):
                        found.append(os.path.join(root, name))
        else:
            found.append(path)
    return found


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

def self_test():
    """Assertions over the masker, the handler walk and the file verdict."""
    import shutil
    import tempfile

    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label):
        try:
            fn()
        except ValueError:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    def run(argv):
        """main() with both streams captured, returning (exit code, output)."""
        buffer = io.StringIO()
        saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = buffer, buffer
        try:
            code = main(argv)
        finally:
            sys.stdout, sys.stderr = saved
        return code, buffer.getvalue()

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

    # ---- python 2, which is 340 of the 743 files this was built for
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

    code, output = run([])
    check(code == 64, "no path at all is a usage error")
    check("--self-test" in output, "the usage error names --self-test")

    print("-" * 68)
    total = passed[0] + len(failed)
    if failed:
        print("%d assertions, %d failed" % (total, len(failed)))
        for label in failed:
            print("  FAILED: %s" % label)
        return 1
    print("%d assertions, 0 failed" % total)
    return 0


# --------------------------------------------------------------------- cli

def _parse(argv):
    ap = argparse.ArgumentParser(
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

    reports = sorted((scan_path(path) for path in iter_sources(args.paths)),
                     key=rank)
    silent = [r for r in reports if r.verdict == SILENT]
    unreadable = [r for r in reports if r.verdict == UNREADABLE]

    if args.json:
        counts = {"silent": len(silent), "unreadable": len(unreadable),
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

    if silent:
        return 1
    if unreadable:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
