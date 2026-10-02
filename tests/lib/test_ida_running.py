import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from hcli.env import ENV
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    _prepare_headless_ida_user_dir,
    find_current_ida_install_directory,
    find_current_ida_version,
    get_ida_path,
    get_ida_user_dir,
    resolve_current_ida_version,
)
from hcli.lib.ida.running import RUNNING_IDA_SOURCE, get_platform_name


@pytest.mark.parametrize(
    ("sys_platform", "machine", "expected"),
    [
        ("darwin", "arm64", "macos-aarch64"),
        ("darwin", "x86_64", "macos-x86_64"),
        ("linux", "aarch64", "linux-aarch64"),
        ("linux", "x86_64", "linux-x86_64"),
        ("win32", "AMD64", "windows-x86_64"),
        ("win32", "ARM64", "windows-aarch64"),
    ],
)
def test_get_platform_name(sys_platform: str, machine: str, expected: str):
    assert get_platform_name(sys_platform, machine) == expected


def test_get_platform_name_rejects_unsupported_platforms():
    with pytest.raises(ValueError):
        get_platform_name("freebsd14", "amd64")
    with pytest.raises(ValueError):
        get_platform_name("linux", "riscv64")


def test_env_reads_ida_variables_when_accessed():
    """A host process can set HCLI_CURRENT_IDA_* after hcli was imported."""
    original = os.environ.get("HCLI_CURRENT_IDA_VERSION")
    os.environ["HCLI_CURRENT_IDA_VERSION"] = "9.9"
    try:
        assert ENV.HCLI_CURRENT_IDA_VERSION == "9.9"
        assert resolve_current_ida_version().version == "9.9"
    finally:
        if original is None:
            del os.environ["HCLI_CURRENT_IDA_VERSION"]
        else:
            os.environ["HCLI_CURRENT_IDA_VERSION"] = original


# Imports hcli before IDA, as a plugin that loads early might, then initializes
# idalib in the same process and reports what hcli resolves.
IDALIB_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import sys

    from hcli.lib.ida import (
        find_current_ida_platform,
        get_ida_user_dir,
        resolve_current_ida_install_directory,
        resolve_current_ida_version,
    )
    from hcli.lib.ida.python import resolve_current_python

    sys.path.insert(0, sys.argv[1])
    try:
        import idapro  # noqa: F401
    except ImportError as e:
        print("__hcli_skip__:" + str(e))
        sys.exit(0)

    install_dir = resolve_current_ida_install_directory()
    version = resolve_current_ida_version()
    python = resolve_current_python()
    result = {
        "install_dir": str(install_dir.path),
        "install_dir_source": install_dir.source,
        "version": version.version,
        "version_source": version.source,
        "user_dir": str(get_ida_user_dir()),
        "platform": find_current_ida_platform(),
        "python_exe": str(python.exe),
        "python_source": python.source,
        "python_probe_version": [python.probe.version_major, python.probe.version_minor] if python.probe else None,
    }

    os.environ["HCLI_CURRENT_IDA_VERSION"] = "9.9"
    result["overridden_version"] = resolve_current_ida_version().version

    print("__hcli__:" + json.dumps(result))
    """
)


@pytest.fixture
def isolated_ida_user_dir() -> Iterator[Path]:
    """An IDAUSR with the license files but no plugins, so idalib starts quickly and predictably."""
    with tempfile.TemporaryDirectory() as temp_dir:
        idausr = Path(temp_dir) / "idausr"
        _prepare_headless_ida_user_dir(get_ida_user_dir(), idausr)
        yield idausr


def test_resolution_inside_idalib_uses_the_running_process(isolated_ida_user_dir: Path):
    try:
        install_dir = find_current_ida_install_directory()
    except MissingCurrentInstallationDirectory:
        pytest.skip("no IDA installation configured")

    idalib_python = get_ida_path(install_dir) / "idalib" / "python"
    if not idalib_python.is_dir():
        pytest.skip("this IDA edition doesn't include idalib")

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HCLI_CURRENT_IDA_")
        and key not in ("HCLI_IDAUSR", "IDAPYTHON_VENV_EXECUTABLE", "PYTHONHOME", "PYTHONPATH")
    }
    env["IDADIR"] = str(get_ida_path(install_dir))
    env["IDAUSR"] = str(isolated_ida_user_dir)

    process = subprocess.run(
        [sys.executable, "-c", IDALIB_SCRIPT, str(idalib_python)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120.0,
        env=env,
        check=False,
    )
    assert process.returncode == 0, process.stderr

    lines = process.stdout.splitlines()
    for line in lines:
        if line.startswith("__hcli_skip__:"):
            pytest.skip(f"idalib can't load here: {line.partition(':')[2]}")

    result = json.loads(next(line for line in lines if line.startswith("__hcli__:")).partition(":")[2])

    assert result["install_dir_source"] == RUNNING_IDA_SOURCE
    assert Path(result["install_dir"]) == install_dir
    assert result["version_source"] == RUNNING_IDA_SOURCE
    assert result["version"] == find_current_ida_version()
    assert Path(result["user_dir"]).resolve() == isolated_ida_user_dir.resolve()
    assert result["platform"].startswith(("macos-", "linux-", "windows-"))
    assert result["python_source"] == RUNNING_IDA_SOURCE
    assert Path(result["python_exe"]).resolve() == Path(sys.executable).resolve()
    assert result["python_probe_version"] == [sys.version_info.major, sys.version_info.minor]
    assert result["overridden_version"] == "9.9"
