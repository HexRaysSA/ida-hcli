# IDA's Python Environment

IDA runs plugins and scripts in an embedded Python interpreter. That interpreter can be a system Python, one you selected with `idapyswitch`, or a virtualenv you created for IDA. `hcli ida python` reaches that same environment from your shell. The packages you install there are the ones IDA imports.

## The recommended setup

HCLI expects IDA's Python to look like this:

- a virtual environment exists, by default at `$IDAUSR/venv`
- it has `pip`
- `IDAPYTHON_VENV_EXECUTABLE` points to that environment's interpreter
- its Python version (major.minor) matches the `libpython` that `idapyswitch` registered for IDA

`IDAPYTHON_VENV_EXECUTABLE` tells IDA which virtualenv to use, however you start IDA: from a terminal, from the Dock, or by opening a file. Other methods, such as `idapythonrc.py` or an activated shell, work only in some situations. HCLI then has to guess which environment IDA uses. Plugins installed into the wrong environment fail to import, with no clear cause.

`hcli ida python create-environment` builds this setup:

```bash
$ hcli ida python create-environment
Python version for the environment: 3.13 (via idat probe)
Creating virtual environment: uv venv --seed --python /opt/homebrew/bin/python3.13 /Users/user/.idapro/venv
Created /Users/user/.idapro/venv with Python 3.13 and pip.
To make IDA use this environment, export IDAPYTHON_VENV_EXECUTABLE in your shell profile:
  export IDAPYTHON_VENV_EXECUTABLE="/Users/user/.idapro/venv/bin/python"
Append this line to /Users/user/.zshrc? [y/n] (n):
```

The command asks IDA, through `idat`, which Python version it runs. It then creates the venv with `uv venv --seed` when `uv` is installed. Otherwise it uses the standard library `venv` module and `ensurepip`. Nothing outside the target directory changes without your consent. HCLI shows the exact shell profile line, or the `setx` command on Windows, before it asks. You can decline and apply it yourself.

Options:

- `--no-configure` skips the question about `IDAPYTHON_VENV_EXECUTABLE`.
- `--path` selects another location.
- `--python-version X.Y` gives the version when `idat` is not available.
- `--json` prints the result as JSON.

HCLI never deletes or modifies an existing directory. If `$IDAUSR/venv` is already a healthy venv with the correct version, the command reports that and exits with success. If it is a venv with another Python version, or an unrelated directory, the command exits non-zero. The message tells you what to remove, or where to point `--path`.

`hcli ida install --create-python-environment` runs the same step after it installs IDA. A new machine gets the recommended setup in one command.

On macOS, shell profiles do not apply to IDA started from Finder or the Dock. For that, run `launchctl setenv IDAPYTHON_VENV_EXECUTABLE <path>`, or start IDA from a terminal.

In Docker containers, the system Python is not writable (PEP 668 externally-managed), so you still need a venv. Use `--create-python-environment` when installing IDA and set `IDAPYTHON_VENV_EXECUTABLE` in the Dockerfile. See [Docker](../advanced/docker/README.md) for a working example.

## Checking the setup

`hcli ida python doctor` reports which IDA and which interpreter HCLI resolved. It names the setup it recognizes and lists each difference from the recommended setup, with a fix:

```bash
$ hcli ida python doctor
IDA installation
  directory: /Applications/IDA Professional 9.4.app (via hcli default instance 'ida-pro-9.4')
  version: 9.4
  user directory ($IDAUSR): /Users/user/.idapro

IDA's Python
  interpreter: /Users/user/.idapro/venv/bin/python3.13 (via derived from idat probe)
  interpreter version: 3.13
  IDA's embedded Python: 3.13
  virtual environment: /Users/user/.idapro/venv
  pip: available
  $IDAPYTHON_VENV_EXECUTABLE: not set

Setup: Virtualenv not configured for IDA
  ...

Warnings (1)
  * $IDAPYTHON_VENV_EXECUTABLE is not set (no-venv-exe-var)
      ...
      Fix:
        export IDAPYTHON_VENV_EXECUTABLE="/Users/user/.idapro/venv/bin/python"
```

Errors are conditions where installing packages fails, or puts them where IDA cannot see them: no virtual environment, no `pip`, an externally managed system Python (PEP 668), a temporary `uv run` environment, or a version mismatch between the venv and IDA's `libpython`. Warnings are setups that work today but are fragile, such as a venv that `IDAPYTHON_VENV_EXECUTABLE` does not select, or a venv activated from `idapythonrc.py`. `doctor` exits non-zero when there are errors. `--json` prints the same report as JSON.

`hcli plugin install` runs this check before it installs Python dependencies. Errors stop the install and print the findings. Warnings only print, and the install continues. The `ida python exec`, `run-script`, and `find-script` commands only print warnings, because you use them to repair the environment. To skip the check, pass `--no-python-environment-check` to the `plugin` or `ida python` group, for example `hcli plugin --no-python-environment-check install <name>`.

When the environment is not what you expect, `hcli ida python explain-environment` shows each step of the detection, from the selected IDA installation to the interpreter it settled on.

## Working in the environment

Everything after `hcli ida python exec` goes to the interpreter:

```bash
$ hcli ida python exec -c "import sys; print(sys.executable)"
/Users/user/.idapro/venv/bin/python

$ hcli ida python exec -m pip --version
pip 25.2 from /Users/user/.idapro/venv/lib/python3.13/site-packages/pip (python 3.13)

$ hcli ida python exec -m pip install requests
```

With no arguments you get an interactive interpreter. HCLI exits with the status the interpreter returned. Arguments reach the interpreter unchanged, including ones HCLI understands elsewhere, so `hcli ida python exec -m pip --help` describes pip. The exception is a leading `--help`, which describes the HCLI command. Write `hcli ida python exec -- --help` for the interpreter's help.

Packages often install command-line programs, such as `capa` from `flare-capa`. These go to the environment's scripts directory, which usually is not on your `PATH`:

```bash
$ hcli ida python find-script capa
/Users/user/.idapro/venv/bin/capa

$ hcli ida python run-script capa --version
capa 9.3.1
```

`find-script` writes only the path to stdout, so you can pass it to other tools. It exits non-zero when nothing is installed under that name. `run-script` forwards the remaining arguments to the program and exits with its status.
