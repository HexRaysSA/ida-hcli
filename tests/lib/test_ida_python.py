import os
import subprocess
import sys
from pathlib import Path

import pytest

from hcli.lib.ida import find_current_ida_install_directory, get_ida_user_dir
from hcli.lib.ida.python import (
    CantInstallPackagesError,
    PipOptions,
    _derive_python_exe,
    does_current_ida_have_pip,
    find_current_python_executable,
    verify_pip_can_install_packages,
)


def has_idat():
    """Check if idat is available (same logic as in test_ida.py)"""
    if "HCLI_HAS_IDAT" not in os.environ:
        return True

    return os.environ["HCLI_HAS_IDAT"].lower() not in ("", "0", "false", "f")


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_returns_path():
    """Test that find_current_python_executable returns a valid path."""
    result = find_current_python_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "python" in result.name.lower()


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_does_current_ida_have_pip():
    python_exe = find_current_python_executable()
    assert does_current_ida_have_pip(python_exe, timeout=30.0)


def _prepare_isolated_idausr_for_python_detection(source_idausr: Path, target_idausr: Path) -> None:
    target_idausr.mkdir()
    (target_idausr / "cfg").mkdir()

    ida_reg = source_idausr / "ida.reg"
    if not ida_reg.exists():
        pytest.skip("Current IDAUSR does not contain ida.reg")
    (target_idausr / "ida.reg").write_bytes(ida_reg.read_bytes())

    for license_file in source_idausr.glob("*.hexlic"):
        (target_idausr / license_file.name).write_bytes(license_file.read_bytes())


def _assert_detected_venv_python(result: Path, venv_dir: Path) -> None:
    if os.name == "nt":
        assert result == venv_dir / "Scripts" / "python.exe"
        return

    assert result.parent == venv_dir / "bin"
    assert result.name.startswith("python")


def _venv_launcher_for_ida(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python3"


def _venv_bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def test_derive_python_exe_honors_validated_virtualenv_executable_when_prefix_is_base(tmp_path):
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("home = /base/python\n", encoding="utf-8")
    bin_dir = _venv_bin_dir(venv_dir)
    bin_dir.mkdir()
    venv_python = _venv_launcher_for_ida(venv_dir)
    venv_python.write_text("", encoding="utf-8")

    info = {
        "frozen": False,
        "prefix": "/Library/Frameworks/Python.framework/Versions/3.14",
        "base_prefix": "/Library/Frameworks/Python.framework/Versions/3.14",
        "executable": str(venv_python),
        "virtual_env": str(venv_dir),
        "idapython_venv_executable": str(venv_python),
        "version_major": 3,
        "version_minor": 14,
    }

    assert _derive_python_exe(info) == venv_python


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_activated_virtualenv(tmp_path, monkeypatch):
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    target_idausr = tmp_path / "idausr-activated"
    _prepare_isolated_idausr_for_python_detection(source_idausr, target_idausr)

    monkeypatch.setenv("HCLI_IDAUSR", str(target_idausr))
    monkeypatch.setenv("HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))
    monkeypatch.setenv("PATH", str(_venv_bin_dir(venv_dir)) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv("IDAPYTHON_VENV_EXECUTABLE", raising=False)
    monkeypatch.delenv("HCLI_CURRENT_IDA_PYTHON_EXE", raising=False)

    result = find_current_python_executable()
    _assert_detected_venv_python(result, venv_dir)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_idapython_venv_executable(tmp_path, monkeypatch):
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    target_idausr = tmp_path / "idausr-venv-executable"
    _prepare_isolated_idausr_for_python_detection(source_idausr, target_idausr)

    monkeypatch.setenv("HCLI_IDAUSR", str(target_idausr))
    monkeypatch.setenv("HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("IDAPYTHON_VENV_EXECUTABLE", str(_venv_launcher_for_ida(venv_dir)))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("HCLI_CURRENT_IDA_PYTHON_EXE", raising=False)

    result = find_current_python_executable()
    _assert_detected_venv_python(result, venv_dir)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_idapythonrc(tmp_path, monkeypatch):
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    target_idausr = tmp_path / "idausr-idapythonrc"
    _prepare_isolated_idausr_for_python_detection(source_idausr, target_idausr)

    (target_idausr / "idapythonrc.py").write_text(
        "import os, site, sys\n"
        "venv = os.environ['HCLI_TEST_VENV']\n"
        'ver = f"{sys.version_info.major}.{sys.version_info.minor}"\n'
        "site.addsitedir(os.path.join(venv, 'lib', f'python{ver}', 'site-packages'))\n"
        "sys.prefix = venv\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HCLI_IDAUSR", str(target_idausr))
    monkeypatch.setenv("HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("HCLI_TEST_VENV", str(venv_dir))
    monkeypatch.delenv("HCLI_CURRENT_IDA_PYTHON_EXE", raising=False)

    result = find_current_python_executable()
    _assert_detected_venv_python(result, venv_dir)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_verify_pip_can_install_packages():
    python_exe = find_current_python_executable()

    verify_pip_can_install_packages(python_exe, ["flare-capa"])

    verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0"])
    verify_pip_can_install_packages(python_exe, ["flare-capa==1.0.0"])
    verify_pip_can_install_packages(python_exe, ["flare-capa==1.0"])
    verify_pip_can_install_packages(python_exe, ["flare-capa==1"])
    verify_pip_can_install_packages(python_exe, ["flare-capa==1"])
    verify_pip_can_install_packages(python_exe, ["flare-capa==v1.2.0"])

    # unfortunately this fuzzy matching doesn't work
    with pytest.raises(CantInstallPackagesError):
        verify_pip_can_install_packages(python_exe, ["flare-capa~=1"])

    # duplicates
    verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa==v1.0.0"])

    # obvious conflict
    with pytest.raises(CantInstallPackagesError):
        verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa==v1.2.0"])

    # unfortunately this doesn't work
    with pytest.raises(CantInstallPackagesError):
        verify_pip_can_install_packages(python_exe, ["flare-capa==1", "flare-capa==v1.2.0"])

    with pytest.raises(CantInstallPackagesError):
        verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa>v1.2.0"])

    with pytest.raises(CantInstallPackagesError):
        verify_pip_can_install_packages(python_exe, ["flare-capa==v1.2.0", "flare-capa<=v1.0.0"])


def test_pip_options_default_builds_empty_args():
    opts = PipOptions()
    assert opts.build_args() == []


def test_pip_options_online_index_url():
    opts = PipOptions(index_url="https://pypi.example.corp/simple")
    args = opts.build_args()
    assert args == ["--index-url", "https://pypi.example.corp/simple"]


def test_pip_options_extra_index_urls():
    opts = PipOptions(extra_index_urls=("https://a.example.com/simple", "https://b.example.com/simple"))
    args = opts.build_args()
    assert "--extra-index-url" in args
    assert args.count("--extra-index-url") == 2


def test_pip_options_find_links_offline():
    opts = PipOptions(find_links=("/tmp/wheelhouse",), offline=True)
    args = opts.build_args()
    assert "--no-index" in args
    assert "--find-links" in args
    assert "/tmp/wheelhouse" in args


def test_pip_options_bundle_mode():
    opts = PipOptions(
        offline=True,
        isolated=True,
        no_cache_dir=True,
        disable_pip_version_check=True,
        find_links=("/tmp/wh",),
    )
    args = opts.build_args()
    assert "--isolated" in args
    assert "--disable-pip-version-check" in args
    assert "--no-cache-dir" in args
    assert "--no-index" in args
    assert "--find-links" in args


def test_pip_options_no_build_isolation():
    opts = PipOptions(no_build_isolation=True)
    args = opts.build_args()
    assert "--no-build-isolation" in args


def test_pip_options_combined_index_and_find_links():
    opts = PipOptions(
        index_url="https://pypi.example.corp/simple",
        find_links=("/local/wheels",),
    )
    args = opts.build_args()
    assert "--index-url" in args
    assert "--find-links" in args
