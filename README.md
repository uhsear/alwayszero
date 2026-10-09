# alwayszero

Name every scheduled script that cannot report failure, on a tree a Python 3 parser cannot read.

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
PASS  except: pass is silent
PASS  except Exception as e with no re-raise is silent
PASS  the python 2 comma form is read as a catch-all  <-- pinned defect
PASS  except BaseException is a catch-all
PASS  python 2 StandardError is a catch-all
PASS  a tuple containing Exception is a catch-all
PASS  a one-line handler body on the except line is read  <-- pinned defect
PASS  a re-raise on the except line itself is read  <-- pinned defect
PASS  a dotted catch-all is matched on its last segment  <-- pinned defect
PASS  a narrow except IOError is not a finding
PASS  a tuple of narrow exceptions is not a finding
PASS  a dotted narrow exception is not a finding
PASS  a handler that re-raises is silent about nothing
PASS  a handler that raises a new error is not quiet
PASS  a handler that exits non-zero is not quiet
PASS  os._exit with a non-zero status is not quiet
PASS  raise SystemExit(3) is not quiet
PASS  a file with no handler at all is clean
PASS  a file whose only exit is sys.exit(0) is still silent  <-- pinned defect
PASS  the report says the exit was a zero one
PASS  one non-zero exit elsewhere in the file clears the verdict
...
PASS  a method named exit on some object is not a process exit
PASS  a reference to sys.exit that is never called is not an exit
...
PASS  AddError with no raise is still quiet  <-- pinned defect
PASS  the note says AddError is a print in a standalone script
PASS  a handler that re-raises after AddError carries no note
...
PASS  logging the traceback does not make a script able to fail  <-- pinned defect
PASS  printing the traceback does not make a script able to fail
PASS  the word raise in a comment does not excuse a handler
PASS  the word raise in a docstring does not excuse a handler
PASS  an except clause inside a string is not a handler
PASS  an exit call inside a string does not clear the file
PASS  masking keeps the line number the reader will open
PASS  a leading UTF-8 byte order mark is not a syntax error  <-- pinned defect
PASS  the import on the line the mark sits on is still an import  <-- pinned defect
PASS  a python 2 print statement is scanned, not skipped
PASS  that same fixture does not parse as python 3  <-- pinned defect
PASS  an unterminated string raises UnreadableSource
PASS  a file tokenize cannot read is UNREADABLE, never CLEAN
PASS  the unreadable report carries the reason
PASS  an unreadable file reports no quiet handlers
PASS  a raise after the dedent is not inside the handler  <-- pinned defect
PASS  a blank line does not end the handler body  <-- pinned defect
PASS  a variable named raised_error is not a re-raise  <-- pinned defect
PASS  two handlers are counted as two sites
PASS  a handler nested in a class is found
PASS  a tab-indented handler body is read
PASS  a nested handler that re-raises clears the handler around it
...
PASS  a trailing comma in an import line is not a module name
PASS  a silent script that notifies is ranked above one that does not
PASS  the report line starts with the verdict
...
PASS  a unique prefix of --self-test is refused  <-- pinned defect
PASS  a tree with a silent script exits 1
PASS  the silent script is named
...
PASS  a .txt file in the tree is not scanned
PASS  both .py files were scanned
PASS  sites are not printed by default
PASS  --sites prints the handler line
PASS  --json reports one silent file
PASS  --json puts the worst verdict first
PASS  --json carries the per-handler detail
PASS  a file that can exit non-zero exits 0
PASS  that file is reported CLEAN
PASS  an unreadable file alone exits 2
PASS  a silent script outranks an unreadable one
PASS  a path that does not exist exits 2, never 0
PASS  a cp1252 comment does not stop the scan  <-- pinned defect
PASS  a byte no codec claims does not stop the scan either
PASS  no path at all is a usage error
PASS  the usage error names --self-test
--------------------------------------------------------------------
106 assertions, 0 failed
```

That run is 106 assertions on Windows under `C:\Python313\python.exe`. The Ubuntu run under
`python3` 3.12.3 gave 104 before the prefix assertion was added, and it has not been re-run since. The lines cut with `...` are more of the same block above them.

## Requirements

Python 3.9 or newer. Standard library only: `tokenize`, `re`, `os`, `io`, `json`, `argparse`. No
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

A scan of four synthetic scripts, which is the whole output:

```
$ python alwayszero.py . --sites
SILENT     .\mail_summary.py  handlers=1 quiet=1 exit=zero  notifies=smtplib
    line 7     except Exception as exc:     QUIET
SILENT     .\nightly_roll.py  handlers=1 quiet=1 exit=none  notifies=ftplib
    line 13    except:                      QUIET
              AddError is a print in a standalone .py, not a failure; only a toolbox turns it red
CLEAN      .\clip_layers.py  handlers=0 quiet=0 exit=none
CLEAN      .\rebuild_index.py  handlers=1 quiet=0 exit=nonzero
    line 5     except Exception:            reports
ALWAYSZERO silent=2 (notifying=2) reports=0 clean=2 unreadable=0 scanned=4
Rewrite the handlers against a run harness that owns the exit code: https://github.com/uhsear/jobharness
```

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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No silent script, and every file was read. |
| 1 | At least one silent script. |
| 2 | Nothing silent, but a file could not be read. |
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
- `.py` and `.pyw` only. A `.bat` wrapper that swallows an exit code is the same disaster and is
  not in scope.
- It reports, it never rewrites. The remedy is a handler that re-raises, or a wrapper that owns
  the exit code.
- On code that parses as Python 3, `ruff` is faster, better maintained and finds more. The gap
  this fills closes a little every year, as the last Python 2 tree is retired.

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
