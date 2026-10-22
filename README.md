# alwayszero

Name every scheduled script that cannot report failure, on a tree a Python 3 parser cannot read.
It also flags the second way a run ends in exit 0: an environment variable that turns it into a
no-op.

Task Scheduler says Last Run Result 0x0. It has said that every night for four years. The script
wraps its work in a bare `except`, prints a sentence into a window nobody watches, and falls off
the end of the file. The process exits 0 whether the FTP connection opened or not.

```python
try:
    ftp.retrbinary("RETR roll.zip", handle.write)
    arcpy.TruncateTable_management(target)
    arcpy.Append_management(staged, target, "NO_TEST")
except:
    print arcpy.GetMessages(2)          # a print. The process still exits 0.
```

The cruel version is the one that looks like reporting. `arcpy.AddError` turns the message red in
an ArcGIS Pro window. In a standalone `.py` under Task Scheduler there is no window, so `AddError`
is a print, and the exit code is still 0. The job that mails the parcel roll to the appraiser
every night has failed since March, the dashboard is green, and nobody looks at a job that keeps
passing.

`ruff` and `pylint` already own the single handler. `E722` flags a bare `except`, `BLE001` a blind
`except Exception`, `S110` a `try/except/pass`, and `pylint` has `W0702` and `W0703`. Run them.
This tool exists for two things they do not do, both measured below: they need a file that parses
as Python 3, and they judge a handler rather than a file.

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
PASS  sys.exit(None) ends the process successfully
...
PASS  AddError with no raise is still quiet  <-- pinned defect
...
PASS  logging the traceback does not make a script able to fail  <-- pinned defect
...
PASS  a leading UTF-8 byte order mark is not a syntax error  <-- pinned defect
PASS  the import on the line the mark sits on is still an import  <-- pinned defect
...
PASS  that same fixture does not parse as python 3  <-- pinned defect
...
PASS  an ERRORTOKEN quote, as python 3.9 yields it, is unreadable  <-- pinned defect
...
PASS  a raise after the dedent is not inside the handler  <-- pinned defect
PASS  a blank line does not end the handler body  <-- pinned defect
PASS  a variable named raised_error is not a re-raise  <-- pinned defect
...
PASS  an environment read that ends the run with exit 0 is ENVNOOP
PASS  the gate names the line of the if
PASS  the gate names the variable
...
PASS  a one-line gate with os.getenv and bare sys.exit() is read
PASS  a missing variable that ends the run is a gate too
PASS  an os.environ subscript names its variable
PASS  a membership test names its variable
PASS  a negated membership test names its variable
...
PASS  an elif is a gate too
...
PASS  a variable name held in a name is not recovered
...
PASS  a name assigned from the environment carries the gate to its if
PASS  the carried variable name comes from the assignment
...
PASS  a name reassigned from the command line is not a gate  <-- pinned defect
PASS  a longer name that starts like the carried one is not it  <-- pinned defect
PASS  an attribute with the carried name is not it  <-- pinned defect
PASS  an == comparison is not an assignment and not an environment read
PASS  a gate that fails loudly is not a no-op
PASS  a gate body that can also exit non-zero is not a no-op
PASS  an environment read with no exit is not a gate
PASS  an exit(0) behind a condition that never reads the environment is fine
PASS  an environment read next to an unconditional exit is fine
PASS  a gate inside a comment or a string is not code
PASS  self.environ is not the process environment  <-- pinned defect
PASS  a condition split across lines is not read  <-- known limit
PASS  a gate written as one expression is not read  <-- known limit
PASS  a bare raise SystemExit is not counted as an exit  <-- known limit
PASS  a keyword argument line is read as an assignment  <-- known limit
PASS  SILENT stays SILENT and still lists the gate
PASS  a gate outranks REPORTS, and the quiet handler is still counted
PASS  a non-zero exit elsewhere does not clear a gate  <-- pinned defect
PASS  ENVNOOP ranks below SILENT and above REPORTS
PASS  the report line names the gate count and the variable
PASS  a gate whose variable name is not literal prints env=?
PASS  a file without a gate has no gates column
PASS  a site line names the gate, its variable and the carrying name
PASS  a direct gate has no via column
PASS  the summary counts ENVNOOP files
PASS  the JSON record carries the gate detail
...
PASS  a unique prefix of --self-test is refused  <-- pinned defect
PASS  a longer unique prefix of --self-test is refused  <-- pinned defect
PASS  there is no write flag, and neither --apply nor a prefix of it parses  <-- pinned defect
...
PASS  a cp1252 comment does not stop the scan  <-- pinned defect
...
PASS  with the variable inherited the fixture exits 0 and does nothing
PASS  without the variable the same fixture does its work
PASS  sys.exit(None) ends the process with status 0
PASS  the scanner reports that same fixture as ENVNOOP, exit 1
PASS  the remedy line names the command line flag
PASS  --json counts the gate and names its variable
PASS  the fix, an explicit command line flag, scans CLEAN, exit 0
PASS  the harness counts a failed check, a missing error and a wrong one
...
--------------------------------------------------------------------
165 assertions, 0 failed
```

That run is 165 assertions on Windows under `C:\Python313\python.exe` (3.13.2). The same 165 pass
on Windows under 3.9.25, 3.10.20 and 3.12, and on Ubuntu under `python3` 3.12.3. The count is equal
on every host because no assertion branches on the platform. The lines cut with `...` are more of
the same block above them: the full run prints one `PASS` line per assertion.

## Requirements

Python 3.9 or newer. Standard library only: `tokenize`, `re`, `os`, `io`, `json`, `argparse`,
and `subprocess`, `runpy`, `shutil` and `tempfile` inside `--self-test`. No
`arcpy`, no ArcGIS licence, no install step, no network and no database connection. It runs on
ArcGIS Pro's Python, on a plain `python3`, and on a machine with no Esri software on it.

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

A scan of five synthetic scripts, which is the whole output. It is a real run, and the exit code
was 1:

```
$ python alwayszero.py . --sites
SILENT     .\mail_summary.py  handlers=1 quiet=1 exit=zero  notifies=smtplib
    line 7     except Exception as exc:     QUIET
SILENT     .\nightly_roll.py  handlers=1 quiet=1 exit=none  notifies=ftplib
    line 13    except:                      QUIET
              AddError is a print in a standalone .py, not a failure; only a toolbox turns it red
ENVNOOP    .\gated_load.py  handlers=0 quiet=0 exit=zero  gates=1 env=LOAD_DEV_MODE
    line 7     env gate                     NOOP env=LOAD_DEV_MODE via=DEV_MODE
CLEAN      .\clip_layers.py  handlers=0 quiet=0 exit=none
CLEAN      .\rebuild_index.py  handlers=1 quiet=0 exit=nonzero
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
3. **The file cannot exit non-zero anywhere.** One `sys.exit(1)`, `os._exit(2)` or
   `raise SystemExit(3)` anywhere in the file clears it, because that one can propagate.
   `sys.exit(0)` does not clear it.

All three hold, and the file is `SILENT`. A `SILENT` file that also imports `smtplib` or `ftplib`
is ranked first, because it talks to a person or a partner on a schedule and cannot say the word
failed. A file with a catch-all handler that can still exit non-zero is `REPORTS`, which is a
note, not a finding.

Comments and strings are masked with `tokenize` before any of this runs, so the word `raise` in a
docstring and an `except:` inside a SQL string are not code. The handler body is then walked by
indentation, never parsed. That is the point: on the 743-file tree this was built from, 403 files
parse with `ast`, and 738 of them tokenize.

Those measurements, from that tree, all reproducible with this tool and `ruff`:

| Measurement | Count |
|---|---|
| `.py` files scanned, in 1.5 seconds | 743 |
| `SILENT` | 101 |
| `SILENT` and importing `smtplib` or `ftplib` | 27 |
| `REPORTS` | 1 |
| `UNREADABLE` | 5 |
| Distinct by MD5 among the 101 | 50 |

Against `ruff 0.16.8 --isolated --select E722,BLE001,S110` on the same tree: `ruff` reports
1,100,071 syntax diagnostics across 316 files and 159 handler findings across 56 files. Of the 101
`SILENT` files, `ruff` flags a handler in 53, reports only syntax errors in 46, and passes 2 with
no finding at all. Of the 27 worst, the ones that also notify somebody, `ruff` reads 6.

The 2 it passes are the second gap, and they are worth more than the 46. Both log the exception
with `logging.error(..., exc_info=True)`, which satisfies `BLE001` correctly: for a library, a
logged exception is handled. For a scheduled job it is not, because the log is not the exit code.
`ruff` also flags a handler in 3 files this tool clears, and it is right to: those handlers are
broad, and those files can still fail. The two tools answer different questions.

My own [pytlint](https://github.com/uhsear/pytlint) asks a closer question. Its `PYT023` fires on a
script entry point that catches everything and still exits 0, and on a file that parses it is the
better tool, because it works from an `ast` and knows which `try` a `raise` sits in. It flags 10 of
those 743 files. It needs two things this tree does not have: an `ast`, which 403 files give it,
and an `if __name__ == "__main__":` guard, which 15 files have. Use `pytlint` on code that parses.
Use this on the tree that is fifteen years old.

## The environment gate

A script has a dev switch. It reads the switch from the environment, because that is the quickest
thing to write, and exits 0 when the switch is on:

```python
DEV_MODE = os.environ.get("LOAD_DEV_MODE")
if DEV_MODE:
    print("dev mode, nothing loaded")
    sys.exit(0)
load_parcels()
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

A file gets the `ENVNOOP` verdict when an `if` or `elif` meets all three conditions:

1. **The condition reads the environment.** `os.environ[...]`, `os.environ.get(...)`,
   `os.getenv(...)`, `"K" in os.environ`, or a bare `environ` after `from os import environ`.
   A name assigned from such a read carries it to a later condition, until the name is assigned
   something else. `DEV = '--dev' in sys.argv` after the read clears it, which is the fix.
2. **The block ends the process with status zero.** `sys.exit()`, `sys.exit(0)`,
   `sys.exit(None)` or `exit(0)` in the block. A block that can also exit
   non-zero, or that has no exit at all, is not a no-op.
3. **The gate sits in the file, not in a comment or a string.** The same masking as above.

A file that is already `SILENT` stays `SILENT` and lists its gates under `--sites`. A file that would be `REPORTS` or `CLEAN` becomes
`ENVNOOP`, because a `sys.exit(1)` somewhere else in the file does not change what the gated
branch does. `ENVNOOP` exits 1, the same polarity as `SILENT`.

What exists today: `ruff` 0.16.8 run with `--select ALL --isolated` on `gated_load.py` reports
four findings (`D100`, `CPY001`, `T201`, `F821`) and none about the gate. I searched GitHub for a
repository named `envnoop` and for tools that detect an environment-gated exit 0. I found none,
which is a search and not a proof. `sys.exit(None)` and `sys.exit(0)` both mean success, and the
`SystemExit` they raise is documented behaviour; see References.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No silent script, no environment gate, and every file was read. |
| 1 | At least one `SILENT` or `ENVNOOP` file. |
| 2 | Neither, but a file could not be read. |
| 64 | Usage error. |

Exit 1 means work was found, so this drives a detect-and-remediate loop the same way
[stalehost](https://github.com/uhsear/stalehost) does. A tree that could not be read is never
reported clean. That is what exit 2 is for.

## Limits

- It reads one file at a time and knows nothing about imports. A script whose handler calls a
  helper in another module, and the helper raises, is still reported `SILENT`. The exit code is
  what it measures, and a raise the scanner cannot see is a raise it will not credit.
- The exit test is whole-file, with no flow analysis. A `sys.exit(1)` inside the `try` block that
  the quiet handler wraps clears the file, and at runtime a bare `except` would catch that
  `SystemExit` and swallow it. The line scanner cannot tell. It is conservative in the direction of
  reporting fewer files, which is the right direction for a tool somebody has to act on.
- A handler that sets a flag, and a later line that exits on the flag, reads as two unrelated
  things. It clears the file, correctly, but for the wrong reason.
- Only `smtplib` and `ftplib` count as notification. `requests`, `urllib` and a `net use` in a
  subprocess do not, because in this corpus they are not how the nightly jobs talk to people.
  The list is in the `CONFIGURATION` block.
- The environment gate is a line scanner too. A condition split over several lines is not read,
  and neither is `os.environ.get("K") and sys.exit(0)` as one expression. A bare
  `raise SystemExit` with no parentheses is not counted as an exit. A scope is not tracked: a
  name assigned from the environment in one function carries into another, and a keyword
  argument line such as `t=1,` inside a call is read as an assignment, which clears a carried `t`.
  A variable name held in another name is flagged, with `env=?`, but not recovered.
- Whether Task Scheduler builds a task's environment from the user's variables was not tested
  here. The self-test shows only that a child process inherits its parent's environment.
- Nothing in this tool writes. There is no `--apply` flag, the parser refuses `--ap` and
  `--apply`, and there is no network code, so there is no stub server to run it against.
- `.py` and `.pyw` only. A `.bat` wrapper that swallows an exit code is the same disaster and is
  not in scope.
- It reports, it never rewrites. The remedy is a handler that re-raises, or a wrapper that owns
  the exit code.
- On code that parses as Python 3, `ruff` is faster, better maintained and finds more. The gap
  this fills closes a little every year, as the last Python 2 tree is retired.

## Verification

| Check | Result |
|---|---|
| `--self-test`, Windows, Python 3.13.2 | 165 assertions, 0 failed |
| `--self-test`, Windows, Python 3.9.25, 3.10.20 and 3.12 | 165 assertions, 0 failed on each |
| `--self-test`, Ubuntu, Python 3.12.3 | 165 assertions, 0 failed |
| `coverage run --branch alwayszero.py --self-test` | 100 percent, 623 statements, 128 branches |
| `pyflakes alwayszero.py`, `python -W error -m py_compile` | clean |

Coverage was measured on Windows under 3.13 only, because `coverage` is not installed on the
Ubuntu host. Python 3.9 and 3.10 take a different path in `tokenize`: they yield an unterminated
one-line string as an `ERRORTOKEN` and do not raise, so an unreadable file was reported `CLEAN`
there before this version. The self-test feeds that token stream to the masker on every host, so
the branch is covered everywhere.

## References

- Python `sys.exit`: `None` is equivalent to zero, and a zero status is successful termination.
  <https://docs.python.org/3.12/library/sys.html#sys.exit>
- Python `os.environ` and `os.getenv`: the mapping is captured at import, and a missing variable
  gives `None` unless a default is passed. <https://docs.python.org/3.12/library/os.html#os.environ>
- Microsoft, Environment Variables: a child process inherits the environment of its parent by
  default, and system variables are set for everyone.
  <https://learn.microsoft.com/en-us/windows/win32/procthread/environment-variables>

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
