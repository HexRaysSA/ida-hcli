# How HCLI Finds IDA

Most HCLI commands need to know which IDA installation to operate on, what version it is, and which Python interpreter it loads. Each of these is resolved by checking a fixed list of sources in order and taking the first answer. `hcli ida python explain-environment` shows every resolution along with the source that produced it, so run that first when detection does something surprising. `hcli ida python doctor` builds on the same data and reports whether the resolved Python matches the recommended setup.

When HCLI runs inside IDA, the running process answers most of these questions. This happens when an IDA plugin imports HCLI as a library, or when an idalib script does so after `import idapro`. HCLI checks for this case by looking for the `ida_kernwin` module, which IDAPython loads before any plugin. It then reads the version, installation directory, user directory, platform, and Python interpreter from the process, so it never launches a second IDA through `idat`. The explicit `HCLI_*` overrides still take precedence.

HCLI reads the IDA-related environment variables (`HCLI_IDAUSR`, `HCLI_CURRENT_IDA_*`, `IDAUSR`, `IDADIR`, and `IDAPYTHON_VENV_EXECUTABLE`) each time it needs them.

## Installation directory

HCLI checks `$HCLI_CURRENT_IDA_INSTALL_DIR` first, which exists as an explicit override for automation. Inside IDA, the directory of the running IDA (`ida_diskio.idadir()`) comes next. Next comes `$IDADIR`, which is set when HCLI runs inside an IDA execution context. After that, HCLI uses its own default instance, registered with `hcli ida set-default /path/to/ida` or `hcli ida install ... --set-default`. Finally it falls back to the `ida-install-dir` entry in `$IDAUSR/ida-config.json`.

On macOS a configured path may be either the `.app` bundle or its inner `Contents/MacOS` directory; both are normalized to the bundle root.

The last two sources answer different questions. The HCLI default instance is HCLI's own selection, stored in HCLI's config. `ida-config.json` is written by IDA itself and consulted by IDA and idalib. They usually agree, but they can diverge, for example after `hcli ida set-default` points at a different installation than the one last launched. When they diverge, HCLI prefers its own default, so HCLI may manage plugins for a different installation than the one idalib would load. Whether these two selections should be unified is an open question; for now, `explain-environment` tells you which source won.

## Version

`$HCLI_CURRENT_IDA_VERSION` overrides everything. Inside IDA, HCLI uses the version of the running kernel (`ida_kernwin.get_kernel_version()`). Otherwise HCLI reads the Windows Add/Remove Programs registry entry for the installation, then the `IDA SDK v9.x` docstring in `python/ida_pro.py` inside the installation, then version metadata embedded in the IDA executable (the PE version resource, the ELF `.ida.version` section, or the Mach-O Info.plist), and as a last resort a `9.x` pattern in the installation directory name.

## Platform

`$HCLI_CURRENT_IDA_PLATFORM` comes first. Inside IDA, HCLI uses `sys.platform` and `platform.machine()` of the running process. Otherwise HCLI reads the architecture from the header of the IDA executable.

## User directory

`$HCLI_IDAUSR` has the highest priority, followed by `ida_diskio.get_user_idadir()` of the running IDA. Otherwise HCLI uses the first entry of `$IDAUSR`, and then the platform default: `%APPDATA%\Hex-Rays\IDA Pro` on Windows and `~/.idapro` elsewhere.

## Python interpreter

`$HCLI_CURRENT_IDA_PYTHON_EXE` overrides everything. Inside IDA, HCLI derives the interpreter from its own `sys.prefix`, `sys.executable`, and environment, with the same code that the `idat` probe runs. Next comes `$IDAPYTHON_VENV_EXECUTABLE` when it points at an existing file. Otherwise HCLI probes IDA itself: it runs `idat` in batch mode, asks the embedded Python for its `sys.prefix`, `sys.executable`, and environment, and derives the interpreter path from that. The probe runs at most once per HCLI invocation.

The probe honors a virtualenv activated by `idapythonrc.py`, so the interpreter HCLI installs plugin dependencies into is the one IDA actually imports from. See [IDA's Python Environment](../user-guide/ida-python-environment.md) for working with that interpreter directly.

HCLI removes `PYTHONHOME`, `PYTHONPATH`, `PYTHONEXECUTABLE`, and `PYTHONSTARTUP` from the environment of every Python interpreter it starts, and of `idat`. These variables describe the Python of HCLI's own process. Inside IDA, `PYTHONHOME` names the prefix of the libpython that IDA loaded, and a virtualenv built on a different base Python fails to import its standard library when it inherits that value.
