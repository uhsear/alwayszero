# alwayszero

Name every scheduled script that cannot report failure, on a tree a Python 3 parser cannot read.
It also flags the second way a run ends in exit 0: an environment variable that turns it into a
no-op.

Task Scheduler says Last Run Result 0x0. It says that every night. The script wraps its work in a
bare `except`, prints a sentence into a window nobody watches, and falls off the end of the file.
The process exits 0 whether the FTP connection opened or not.

```python
try:
    ftp.retrbinary("RETR extract.zip", handle.write)
    arcpy.TruncateTable_management(target)
    arcpy.Append_management(staged, target, "NO_TEST")
except:
    print arcpy.GetMessages(2)          # a print. The process still exits 0.
```

The cruel version is the one that looks like reporting. `arcpy.AddError` turns the message red in
an ArcGIS Pro window. In a standalone `.py` under Task Scheduler there is no window, so `AddError`
is a print, and the exit code is still 0. The job that mails a nightly extract to its customer can
fail for weeks while the dashboard stays green, and nobody looks at a job that keeps passing.

`ruff` and `pylint` already own the single handler. `E722` flags a bare `except`, `BLE001` a blind
`except Exception`, `S110` a `try/except/pass`, and `pylint` has `W0702` and `W0703`. Run them.
This tool exists for two things they do not do: they need a file that parses as Python 3, and they
judge a handler rather than a file. Both are shown below on synthetic files.

```
$ python alwayszero.py --self-test
alwayszero self-test: no arcpy, no network, no corpus
--------------------------------------------------------------------
PASS  a bare except whose body is only a print is silent
...
PASS  the python 2 comma form is read as a catch-all  <-- pinned defect
...
PASS  a one-line handler body on the except line is read  <-- pinned defect
PASS  a re-raise on the except line itself is read  <-- pinned defect
PASS  a dotted catch-all is matched on its last segment  <-- pinned defect
...
PASS  a file whose only exit is sys.exit(0) is still silent  <-- pinned defect
...
PASS  sys.exit(main()) is a status the scanner cannot evaluate  <-- pinned defect
PASS  nested parentheses in an exit argument are balanced  <-- pinned defect
...
PASS  the site builtin quit is an exit too  <-- pinned defect
PASS  sys.exit(False) is a zero status  <-- pinned defect
...
PASS  a main() whose handler returns 1 and an exit through main() is not silent  <-- pinned defect
PASS  sys.exit with a message exits 1, so the handler is not quiet  <-- pinned defect
...
PASS  AddError with no raise is still quiet  <-- pinned defect
...
PASS  logging the traceback does not make a script able to fail  <-- pinned defect
...
PASS  a leading UTF-8 byte order mark is not a syntax error  <-- pinned defect
PASS  the import on the line the mark sits on is still an import  <-- pinned defect
PASS  the word raise inside an f-string is not code  <-- pinned defect
PASS  a doubled brace in an f-string does not unmask its text  <-- pinned defect
PASS  a triple-quoted f-string over several rows is masked  <-- pinned defect
PASS  sys.exit with an f-string message exits 1  <-- pinned defect
...
PASS  a form feed does not shift the masks  <-- pinned defect
PASS  a form feed before the handler does not hide it  <-- pinned defect
PASS  U+2028 in a comment does not shift the masks  <-- pinned defect
...
PASS  a NUL byte is unreadable on every Python  <-- pinned defect
...
PASS  a SystemError from the tokenizer is unreadable  <-- pinned defect
PASS  a UnicodeDecodeError from the tokenizer is unreadable  <-- pinned defect
PASS  a token with a negative column, as python 3.13 gives an unterminated f-string, is unreadable  <-- pinned defect
PASS  an unterminated f-string never crashes the scan  <-- pinned defect
...
PASS  that same fixture does not parse as python 3  <-- pinned defect
...
PASS  an ERRORTOKEN quote, as python 3.9 yields it, is unreadable  <-- pinned defect
...
PASS  a raise after the dedent is not inside the handler  <-- pinned defect
PASS  a blank line does not end the handler body  <-- pinned defect
PASS  a variable named raised_error is not a re-raise  <-- pinned defect
...
PASS  a name reassigned from the command line is not a gate  <-- pinned defect
PASS  a longer name that starts like the carried one is not it  <-- pinned defect
PASS  an attribute with the carried name is not it  <-- pinned defect
...
PASS  self.environ is not the process environment  <-- pinned defect
PASS  a condition split across lines is read, at the line of the if  <-- pinned defect
...
PASS  a gate written as one expression is not read  <-- known limit
PASS  a bare raise SystemExit is not counted as an exit  <-- known limit
PASS  a keyword argument line is read as an assignment  <-- known limit
PASS  an exit(0) in the else of an environment test is a gate  <-- pinned defect
...
PASS  a gate block that does the work before exit(0) is not a no-op  <-- pinned defect
...
PASS  print_report() is work, not a print  <-- pinned defect
...
PASS  a non-zero exit elsewhere does not clear a gate  <-- pinned defect
...
PASS  a unique prefix of --self-test is refused  <-- pinned defect
PASS  a longer unique prefix of --self-test is refused  <-- pinned defect
PASS  there is no write flag, and neither --apply nor a prefix of it parses  <-- pinned defect
...
PASS  an unknown flag is a usage error, exit 64, not 2  <-- pinned defect
PASS  a refused prefix is exit 64 too  <-- pinned defect
...
PASS  a character the output encoding cannot hold is written as an escape  <-- pinned defect
...
PASS  a report on a file with an unencodable name still prints and exits 1  <-- pinned defect
...
PASS  a directory the walk could not list is returned as a failure  <-- pinned defect
...
PASS  an unlistable root is one UNREADABLE report, so the run cannot be clean  <-- pinned defect
PASS  a directory the account cannot list is UNREADABLE and exits 2  <-- pinned defect
...
PASS  a cp1252 comment does not stop the scan  <-- pinned defect
...
--------------------------------------------------------------------
249 assertions, 0 failed
```

That run is 249 assertions on Windows under `C:\Python313\python.exe` (3.13.2). The same 249 pass on Windows under 3.12.10, 3.11.15, 3.10.20 and 3.9.25, and on Ubuntu under `python3` 3.12.3. The count is equal on every host because no assertion is skipped on any platform. The lines cut with `...` are more of the same block above them: the full run prints one `PASS` line per assertion.

## Requirements

Python 3.9 or newer. Standard library only: `tokenize`, `re`, `os`, `io`, `json` and `argparse`,
and `ast`, `contextlib`, `getpass`, `runpy`, `shutil`, `subprocess` and `tempfile` inside
`--self-test`. No `arcpy`, no ArcGIS licence, no install step, no network and no database
connection. It runs on ArcGIS Pro's Python, on a plain `python3`, and on a machine with no Esri
software on it.

```
git clone https://github.com/uhsear/alwayszero.git
```

## Quick start

```
python alwayszero.py --self-test
python alwayszero.py D:\GIS\Scripts
```

## Usage

```
python alwayszero.py nightly_job.py
python alwayszero.py D:\GIS\Scripts
python alwayszero.py D:\GIS\Scripts --sites
python alwayszero.py D:\GIS\Scripts --json
```

A scan of five synthetic scripts, which is the whole output. The exit code was 1. Paths are shown
with forward slashes. `--self-test` rebuilds these five scripts in a temporary folder and
compares the report line for line, so this block cannot drift from the tool:

```
$ python alwayszero.py . --sites
SILENT     ./mail_summary.py  handlers=1 quiet=1 exit=zero  notifies=smtplib
    line 12    except Exception as exc:     QUIET
SILENT     ./nightly_extract.py  handlers=1 quiet=1 exit=none  notifies=ftplib
    line 8     except:                      QUIET
              AddError is a print in a standalone .py, not a failure; only a toolbox turns it red
ENVNOOP    ./gated_load.py  handlers=0 quiet=0 exit=zero  gates=1 env=LOAD_DEV_MODE
    line 5     env gate                     NOOP env=LOAD_DEV_MODE via=DEV_MODE
CLEAN      ./clip_layers.py  handlers=0 quiet=0 exit=none
CLEAN      ./rebuild_index.py  handlers=1 quiet=0 exit=nonzero
    line 5     except Exception:            reports
ALWAYSZERO silent=2 (notifying=2) envnoop=1 reports=0 clean=2 unreadable=0 scanned=5
Rewrite the handlers against a run harness that owns the exit code: https://github.com/uhsear/jobharness
Move each environment switch to an explicit command line flag, so a scheduler has to type it.
```

`gated_load.py` reads `LOAD_DEV_MODE` into `DEV_MODE` on one line and exits 0 on another. The
`via=DEV_MODE` column says the read reached the `if` through that name.

`rebuild_index.py` is the shape that clears the check. It catches `Exception`, and somewhere in
the file it calls `sys.exit(1)`, so the process can still end non-zero. `mail_summary.py` is the
shape that does not: its only exit is `sys.exit(0)`, which is the same lie as no exit at all, and
the `exit=zero` column says which one it was.

## What it checks

The verdict is for the whole file, and three conditions decide it.

1. **A catch-all handler.** A bare `except`, or one naming `Exception`, `BaseException` or the
   Python 2 `StandardError`. A narrow `except arcpy.ExecuteError` is deliberate control flow and
   is never a finding.
2. **The handler is quiet.** Its body neither re-raises nor exits non-zero. Printing is quiet.
   Logging is quiet. `arcpy.AddError` is quiet, and the report says why.
3. **The file cannot exit non-zero anywhere.** One `sys.exit(1)`, `os._exit(2)`, `quit(1)` or
   `raise SystemExit(3)` anywhere in the file clears it, because that one can propagate.
   `sys.exit(0)` does not clear it. A status the scanner cannot evaluate, such as
   `sys.exit(main())`, counts as able to fail. `sys.exit("message")` also exits 1: Python prints
   the string and sets the status to 1.

All three hold, and the file is `SILENT`. A `SILENT` file that also imports `smtplib` or `ftplib`
is ranked first, because it talks to a person or a partner on a schedule and cannot say the word
failed. A file with a catch-all handler that can still exit non-zero is `REPORTS`, which is a
note, not a finding.

Comments and strings are masked with `tokenize` before any of this runs, so the word `raise` in a
docstring and an `except:` inside a SQL string are not code. That includes an f-string, which
Python 3.12 and later split into several tokens. The handler body is then walked by indentation,
never parsed. That is the point: a Python 2 file does not parse with `ast`, and `tokenize` still
reads it.

Two checks you can run on files this size:

```
$ cat py2_handler.py
try:
    print 'working'
except:
    print 'failed'
$ ruff check --isolated --select E722,BLE001,S110 py2_handler.py
py2_handler.py:2:11: invalid-syntax: Simple statements must be separated by newlines or semicolons
py2_handler.py:4:11: invalid-syntax: Simple statements must be separated by newlines or semicolons
Found 2 errors.
$ python alwayszero.py py2_handler.py
SILENT     py2_handler.py  handlers=1 quiet=1 exit=none
```

`ruff` 0.16.8 reads only syntax errors there, and the bare `except` goes unreported. The second
gap is the logged exception. This file passes `BLE001`:

```
import logging

try:
    go()
except Exception as exc:
    logging.error("job failed: %s", exc, exc_info=True)
```

The `ruff` documentation says an exception logged with `exc_info` is not flagged, which is right
for a library, where a logged exception is handled. For a scheduled job it is not, because the log
is not the exit code. This tool reports that file as `SILENT`.

`ruff` also flags handlers in files this tool clears, and it is right to: on the five-script sample
below it reports `rebuild_index.py` with `BLE001`, because the handler is broad, and this tool
reports the file `CLEAN`, because the file can still fail. The two tools answer different questions.

My own [pytlint](https://github.com/uhsear/pytlint) asks a closer question. Its `PYT023` fires on a
script entry point that catches every exception and still exits 0, and on a file that parses it is
the better tool, because it works from an `ast` and knows which `try` a `raise` sits in. Use
`pytlint` on code that parses. Use this on the tree that does not.

No counts from the private tree this tool was built against appear here. It cannot be published,
so no reader could repeat a count taken from it. Every claim on this page is shown on a synthetic
script, and `--self-test` runs each one.

## The environment gate

A script has a dev switch. It reads the switch from the environment, because that is the quickest
thing to write, and exits 0 when the switch is on:

```python
DEV_MODE = os.environ.get("LOAD_DEV_MODE")
if DEV_MODE:
    print("dev mode, nothing loaded")
    sys.exit(0)
load_roads()
```

Somebody sets `LOAD_DEV_MODE` once, to test on their own login, and forgets it. A child process
inherits the environment of its parent by default, and a system-level variable is set for
everyone. The next scheduled run starts with the variable in place, prints one line into a
window nobody watches, and ends with exit 0. The scheduler reports success every night and
nothing is loaded. This is the same disaster as the silent handler, reached without a single
`except`. It was a near miss on the tree this tool was built from, not a recorded loss. The fix is
an explicit `--dev` flag, which a scheduler has to type on purpose. The self-test runs a fixture
of this shape in a child process, with and without the variable, and checks both outcomes: with
it, exit 0 and no work done; without it, the work is done.

A file gets the `ENVNOOP` verdict when an `if`, `elif` or `else` meets all three conditions:

1. **The test reads the environment.** `os.environ[...]`, `os.environ.get(...)`,
   `os.getenv(...)`, `"K" in os.environ`, or a bare `environ` after `from os import environ`.
   A name assigned from such a read carries it to a later test, until the name is assigned
   something else. `DEV = '--dev' in sys.argv` after the read clears it, which is the fix. A test
   that runs over several rows is read, up to 20 further rows. An `else` is a gate when any `if` or
   `elif` in its chain reads the environment, because the `else` runs when the variable is unset.
2. **The block only talks, then ends the process with status zero.** `pass`, `print`, a log call,
   an `arcpy` message or a write to stdout or stderr may come first. Then `sys.exit()`,
   `sys.exit(0)`, `sys.exit(None)`, `exit(0)` or `quit()`. A block that does any other work
   before it exits has done its work, and its exit is the end of a normal branch. A block that can
   also exit non-zero is not a no-op.
3. **The gate sits in the file, not in a comment or a string.** The same masking as above.

A file that is already `SILENT` stays `SILENT` and lists its gates under `--sites`. A file that
would be `REPORTS` or `CLEAN` becomes `ENVNOOP`, because a `sys.exit(1)` somewhere else in the file
does not change what the gated branch does. `ENVNOOP` exits 1, the same polarity as `SILENT`.

What exists today: `ruff` 0.16.8 run with `--select ALL --isolated` on `gated_load.py` reports four
findings (`D100`, `CPY001`, `T201`, `F821`) and none about the gate. I searched GitHub with
`gh search repos` for `envnoop` and for `alwayszero`. Only this repository came back. That is a
search and not a proof. `sys.exit(None)` and `sys.exit(0)` both mean success, and the `SystemExit`
they raise is documented behaviour; see References.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No silent script, no environment gate, and every file and directory was read. |
| 1 | At least one `SILENT` or `ENVNOOP` file. |
| 2 | Neither, but a file or a directory could not be read. |
| 64 | Usage error: no path given, or a flag the parser refuses, including a prefix of a flag. |

Exit 1 means work was found, so this drives a detect-and-remediate loop the same way
[stalehost](https://github.com/uhsear/stalehost) does. A tree that could not be read is never
reported clean. That is what exit 2 is for, and it covers a directory the account cannot list:
`os.walk` skips such a directory unless it is given an `onerror` function, so this tool gives it
one and reports the directory as `UNREADABLE`.

## Limits

- It reads one file at a time and knows nothing about imports. A script whose handler calls a
  helper in another module, and the helper raises, is still reported `SILENT`. The exit code is
  what it measures, and a raise the scanner cannot see is a raise it will not credit.
- The exit test is whole-file, with no flow analysis. A `sys.exit(1)` inside the `try` block that
  the quiet handler wraps clears the file, and at runtime a bare `except` would catch that
  `SystemExit` and swallow it. The line scanner cannot tell. It is conservative in the direction of
  reporting fewer files, which is the right direction for a tool somebody has to act on.
- A handler that returns 1 is not an exit. A file whose handler returns 1 and ends with
  `sys.exit(main())` is cleared by the computed status, and `main()` may still return `None`.
  A computed exit status such as `sys.exit(main())` is assumed to be able to fail.
- A handler that sets a flag, and a later line that exits on the flag, reads as two unrelated
  things. It clears the file, correctly, but for the wrong reason.
- Only `smtplib` and `ftplib` count as notification. `requests`, `urllib` and a `net use` in a
  subprocess do not. The list is in the `CONFIGURATION` block.
- Only `sys.exit`, `exit`, `quit`, `os._exit` and `SystemExit(...)` count as ending the run. A
  `return` from `main()` is not seen, so a gate written as `if os.environ.get("K"): return` scans
  clean. A bare `raise SystemExit` with no parentheses is not counted, and neither is
  `os.sys.exit(1)`.
- Only plain names are tracked as assignments from the environment. A read through an attribute
  (`self.dev = os.environ...`), an annotated assignment (`dev: bool = ...`), an `argparse`
  `default=`, or an aliased import (`import os as o`, `from os import getenv as g`) is not
  followed. A `while os.environ.get(...)` loop is not read as a gate. A gate written as one
  expression, `os.environ.get("K") and sys.exit(0)`, is not read.
- Any later assignment to a carried name clears it, even one that still depends on the
  environment: `DEV = DEV or "--dev" in sys.argv` clears the read. A keyword argument row such as
  `t=1,` inside a call is read as an assignment too.
- Scope is not tracked. A name assigned from the environment in one function carries into every
  later test in the file, including a parameter or a local that shares the name in another
  function. A very large file with many such names makes the gate scan slow.
- A path or setting read from the environment and then tested is reported when the test ends the
  run quietly: `if os.path.exists(OUT_DIR): print("done"); sys.exit(0)` is an ordinary idempotence
  check that an environment variable can still defeat.
- Environment variable names are read from the unmasked line, so a `getenv("...")` call in a
  trailing comment on a gate line can be reported as a variable name. A name held in a variable is
  flagged with `env=?`, not recovered.
- Whether Task Scheduler builds a task's environment from the user's variables was not tested
  here. The self-test shows only that a child process inherits its parent's environment.
- A directory that holds no `.py` or `.pyw` file is reported clean with `scanned=0`. A mistyped
  path that exists, or an empty mount, looks the same, so read the `scanned=` count.
- Exit 1 does not say that some files were also unreadable. Read the `unreadable=` count. An
  uncaught Python exception also exits 1, the same code as a finding.
- UTF-16 source files contain NUL bytes. They are reported `UNREADABLE` and never scanned.
  Re-save them as UTF-8.
- The number of readable files depends on the Python version. Python 3.12 and later reject
  inconsistent tab and space indentation that 3.11 and earlier accept, so a Python 2 file that mixes
  them is `UNREADABLE` on one and `SILENT` on the other. The self-test asserts nothing about it.
  Every other assertion has the same result on 3.9 through 3.13.
- Nothing in this tool writes. There is no `--apply` flag, the parser refuses `--ap` and
  `--apply`, and there is no network code, so there is no stub server to run it against. The
  self-test writes temporary files, and for one assertion it denies listing of a temporary folder:
  `chmod 0` on Linux, an `icacls` deny entry on Windows. It lifts the denial before it deletes the
  folder. An account that can list the folder anyway, such as root, makes that assertion vacuous.
- `.py` and `.pyw` only. A `.bat` wrapper that swallows an exit code is the same disaster and is
  not in scope.
- It reports, it never rewrites. The remedy is a handler that re-raises, or a wrapper that owns
  the exit code.
- On code that parses as Python 3, `ruff` is faster, better maintained and finds more. The gap
  this fills closes a little every year, as the last Python 2 tree is retired.

## Verification

| Check | Result |
|---|---|
| `--self-test`, Windows, Python 3.13.2 | 249 assertions, 0 failed |
| `--self-test`, Windows, Python 3.12.10, 3.11.15, 3.10.20 and 3.9.25 | 249 assertions, 0 failed on each |
| `--self-test`, Ubuntu, Python 3.12.3 | 249 assertions, 0 failed |
| `coverage run --branch alwayszero.py --self-test` | 100 percent, 911 statements, 176 branches |
| `pyflakes alwayszero.py`, `python -W error -m py_compile` | clean |
| A line-level mutation sweep of the decision core, once | 267 mutants, 45 survivors, none that changes a result |

Coverage was measured on Windows under 3.13 only, because `coverage` is not installed on the
Ubuntu host. Python 3.9 and 3.10 take a different path in `tokenize`: they yield an unterminated
one-line string as an `ERRORTOKEN` and do not raise, and 3.13.2 gives negative columns for some
unterminated f-strings. The self-test feeds those token streams to the masker on every host, so
each branch is covered everywhere. The sweep is not in this repository. It changed one operator or
dropped one statement per run and re-ran the self-test; the survivors are in docstrings, comments,
help text and messages, or are changes that cannot alter a result, such as a `continue` before a line that
matches nothing.

## References

- Python `sys.exit`: `None` is equivalent to zero, and any other object is printed to stderr and
  gives exit code 1. `sys.exit("some error message")` is the documented quick exit on error.
  <https://docs.python.org/3.12/library/sys.html#sys.exit>
- Python `exit` and `quit`: when called, they raise `SystemExit` with the given exit code.
  <https://docs.python.org/3.12/library/constants.html#exit>
- Python `os.environ`: the mapping is captured the first time `os` is imported.
  <https://docs.python.org/3.12/library/os.html#os.environ>
- Python `os.walk`: errors from the `scandir()` call are ignored unless `onerror` is given, and the
  filename is the `filename` attribute of the exception it receives.
  <https://docs.python.org/3.12/library/os.html#os.walk>
- Python `token`: from 3.12 an f-string is `FSTRING_START`, `FSTRING_MIDDLE` and `FSTRING_END`
  tokens. <https://docs.python.org/3.12/library/token.html>
- Microsoft, Environment Variables: a child process inherits the environment of its parent by
  default, and system variables are set for everyone.
  <https://learn.microsoft.com/en-us/windows/win32/procthread/environment-variables>
- Ruff `blind-except` (`BLE001`): an exception logged with `exc_info` enabled is not flagged.
  <https://docs.astral.sh/ruff/rules/blind-except/>

## Related

Other single-file tools in this portfolio that pair with this one:

- [jobharness](https://github.com/uhsear/jobharness) - the wrapper these handlers should have been written against, with logging, retry, resume and an exit code that means something
- [pytlint](https://github.com/uhsear/pytlint) - the same disaster on a file that parses, judged from an `ast`, plus every `.pyt` toolbox rule
- [taskpulse](https://github.com/uhsear/taskpulse) - the other end of the same lie, read from Windows Task Scheduler rather than from the source
- [clonedrift](https://github.com/uhsear/clonedrift) - which of the copies of that script on the share is the one the scheduler actually runs

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.
