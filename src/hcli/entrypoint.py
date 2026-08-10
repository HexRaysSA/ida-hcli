"""Low-overhead console entrypoint for HCLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Match hcli.main before computing cache fingerprints or launching subprocesses.
os.environ["PYTHONUTF8"] = "1"


def _run_find_script(name: str) -> int:
    """Run find-script without importing the full Click command tree."""
    from hcli.fast_cli import cache_script_result
    from hcli.lib.ida.python import (
        ProbeError,
        PythonNotFoundError,
        get_script_info,
        render_script_not_found,
        resolve_current_python,
    )

    try:
        python_exe = resolve_current_python().exe
        info = get_script_info(python_exe, name)
    except (PythonNotFoundError, ProbeError) as e:
        print(str(e), file=sys.stderr)
        return 1

    if info.path is None:
        print(render_script_not_found(info), file=sys.stderr)
        return 1

    cache_script_result(python_exe, name, info.path, info.cache_paths)
    print(Path(info.path))
    return 0


def main() -> int | None:
    from hcli.fast_cli import parse_find_script_invocation, try_fast_find_script, try_fast_run_script

    argv = sys.argv[1:]
    run_status = try_fast_run_script(argv)
    if run_status is not None:
        return run_status
    if try_fast_find_script(argv):
        return 0

    # A cache miss still does not need auth, HTTP, plugin-manager, prompt, update,
    # or Click command imports. Keep the cold path scoped to IDAPython detection.
    name = parse_find_script_invocation(argv)
    if name is not None and not os.environ.get("HCLI_DEBUG"):
        return _run_find_script(name)

    from hcli.main import cli

    cli()
    return None
