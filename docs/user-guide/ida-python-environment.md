# IDA's Python Environment

IDA runs plugins and scripts in an embedded Python interpreter. That interpreter might be a system Python, one you selected with `idapyswitch`, or a virtualenv you created for IDA. `hcli ida python` reaches that same environment from your shell, so the packages you install are the ones IDA will import.

## The recommended setup

HCLI expects IDA's Python to look like this:

- a virtual environment exists, by default at `$IDAUSR/venv`
- it has `pip`
- `IDAPYTHON_VENV_EXECUTABLE` points at that environment's interpreter
- its Python version (major.minor) matches the `libpython` that `idapyswitch` registered for IDA

`IDAPYTHON_VENV_EXECUTABLE` is how IDA itself learns which virtualenv to use, regardless of how it was launched: from a terminal, from the Dock, or by double-clicking a file. Other ways of activating a venv, such as `idapythonrc.py` or starting IDA from an activated shell, work in some situations and not others, and HCLI then has to guess which environment IDA ends up with. Plugins installed into the wrong environment fail to import with no obvious cause.

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

It asks IDA (through `idat`) which Python version it runs, then creates the venv with `uv venv --seed` when `uv` is installed, and otherwise with the standard library `venv` module plus `ensurepip`. Nothing outside the target directory changes without your consent: the exact shell-profile line (or `setx` command on Windows) is shown before you are asked, and you can decline and apply it yourself. Pass `--no-configure` to skip that question, `--path` to choose another location, and `--python-version X.Y` for when `idat` isn't available to answer. `--json` prints the result as a JSON object for scripting.

An existing directory is never deleted or modified. If `$IDAUSR/venv` already is a healthy venv of the right version, the command succeeds and only reports it. If it is a venv of another Python version, or some unrelated directory, the command exits non-zero and tells you what to remove or where else to point `--path`.

`hcli ida install --create-python-environment` runs the same step right after installing IDA, so a fresh machine ends up with the recommended setup in one command.

On macOS, shell profiles do not apply to IDA started from Finder or the Dock. Run `launchctl setenv IDAPYTHON_VENV_EXECUTABLE <path>` for that case, or start IDA from a terminal.

## Checking the setup

`hcli ida python doctor` reports which IDA and which interpreter HCLI resolved, names the setup it recognizes, and lists what differs from the recommended setup with a fix for each item:

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

Errors are conditions under which installing packages will fail or will put them where IDA can't see them: no virtual environment, no `pip`, an externally managed system Python (PEP 668), a `uv run` ephemeral environment, or a version mismatch between the venv and IDA's `libpython`. Warnings are conditions that work today but are fragile, such as a venv that `IDAPYTHON_VENV_EXECUTABLE` doesn't name or a venv activated from `idapythonrc.py`. `doctor` exits non-zero when there are errors, and `--json` prints the same report as JSON.

`hcli plugin install` runs this check before installing Python dependencies. Errors stop the install with the same findings and the suggestion to run `doctor`; warnings are printed and the install continues. The `ida python exec`, `run-script`, and `find-script` commands only print warnings, since they are the tools you use to repair the environment. To bypass the check, pass `--no-python-environment-check` to the `plugin` or `ida python` group, for example `hcli plugin --no-python-environment-check install <name>`.

When the environment isn't what you expect, `hcli ida python explain-environment` shows every step of the detection, from the selected IDA installation through to the interpreter it settled on.

## Working in the environment

Everything after `hcli ida python exec` goes to the interpreter:

```bash
$ hcli ida python exec -c "import sys; print(sys.executable)"
/Users/user/.idapro/venv/bin/python

$ hcli ida python exec -m pip --version
pip 25.2 from /Users/user/.idapro/venv/lib/python3.13/site-packages/pip (python 3.13)

$ hcli ida python exec -m pip install requests
```

With no arguments you get an interactive interpreter, and HCLI exits with whatever status the interpreter returned. Arguments reach the interpreter untouched, including ones HCLI understands elsewhere, so `hcli ida python exec -m pip --help` describes pip. The exception is a leading `--help`, which describes the HCLI command itself; write `hcli ida python exec -- --help` for the interpreter's.

Packages often install command-line programs, such as `capa` from `flare-capa`. These end up in the environment's scripts directory, which usually isn't on your `PATH`:

```bash
$ hcli ida python find-script capa
/Users/user/.idapro/venv/bin/capa

$ hcli ida python run-script capa --version
capa 9.3.1
```

`find-script` writes just the path to stdout, so you can hand it to other tooling, and exits non-zero when nothing is installed under that name. `run-script` forwards the remaining arguments to the program and exits with its status.
