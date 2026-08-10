# IDA's Python Environment

IDA runs plugins and scripts in an embedded Python interpreter. That interpreter might be a system Python, one you selected with `idapyswitch`, or a virtualenv activated by your `idapythonrc.py`. `hcli ida python` reaches that same environment from your shell, so the packages you install are the ones IDA will import.

Everything after `hcli ida python exec` goes to the interpreter:

```bash
$ hcli ida python exec -c "import sys; print(sys.executable)"
/Users/user/.idapro/venv/bin/python

$ hcli ida python exec -m pip --version
pip 25.2 from /Users/user/.idapro/venv/lib/python3.13/site-packages/pip (python 3.13)

$ hcli ida python exec -m pip install requests
```

With no arguments you get an interactive interpreter, and HCLI exits with whatever status the interpreter returned. Arguments reach the interpreter untouched, including ones HCLI understands elsewhere, so `hcli ida python exec -m pip --help` describes pip. The exception is a leading `--help`, which describes the HCLI command itself; write `hcli ida python exec -- --help` for the interpreter's.

Packages often install command-line programs, such as `capa` from `flare-capa`. These land in the environment's scripts directory, which usually isn't on your `PATH`:

```bash
$ hcli ida python find-script capa
/Users/user/.idapro/venv/bin/capa

$ hcli ida python run-script capa --version
capa 9.3.1
```

`find-script` writes just the path to stdout, so you can hand it to other tooling, and exits non-zero when nothing is installed under that name. `run-script` forwards the remaining arguments to the program and exits with its status.

When the environment isn't what you expect, such as the wrong interpreter or a virtualenv IDA doesn't pick up, `hcli ida python explain-environment` shows every step of the detection, from the selected IDA installation through to the interpreter it settled on. It's the first thing to run when a plugin installs but won't import.
