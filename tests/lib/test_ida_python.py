import os
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures import set_env_var, unset_env_var

from hcli.lib.ida import find_current_ida_install_directory, get_ida_user_dir
from hcli.lib.ida.python import (
    CantInstallPackagesError,
    IdatProbe,
    PipOptions,
    ResolvedPython,
    _derive_python_exe,
    find_current_python_executable,
    find_python_version_mismatches,
    format_python_version_mismatch_warning,
    has_pip,
    is_externally_managed,
    merge_bundle_pip_options,
    pip_install_packages,
    probe_current_python_info,
    resolve_current_python,
    verify_pip_can_install_packages,
)
from hcli.lib.venv import get_virtual_env_version, probe_python_version


def has_idat():
    """Check if idat is available (same logic as in test_ida.py)"""
    if "HCLI_HAS_IDAT" not in os.environ:
        return True

    return os.environ["HCLI_HAS_IDAT"].lower() not in ("", "0", "false", "f")


def _venv_bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def _venv_launcher_for_ida(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python3"


def _write_fake_venv(venv_dir: Path, version: str) -> Path:
    """Create a venv layout whose interpreter can't be run, so pyvenv.cfg decides the version."""
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(f"home = /base/python\nversion = {version}\n", encoding="utf-8")
    bin_dir = _venv_bin_dir(venv_dir)
    bin_dir.mkdir(exist_ok=True)
    python = _venv_launcher_for_ida(venv_dir)
    python.write_text("", encoding="utf-8")
    return python


def _ida_info(version: tuple[int, int], **overrides) -> IdatProbe:
    info = {
        "frozen": False,
        "prefix": "/opt/python3",
        "base_prefix": "/opt/python3",
        "executable": "/opt/python3/bin/python3",
        "virtual_env": None,
        "idapython_venv_executable": None,
        "version_major": version[0],
        "version_minor": version[1],
    }
    info.update(overrides)
    return IdatProbe.model_validate(info)


def _prepare_isolated_idausr_for_python_detection(source_idausr: Path, target_idausr: Path) -> None:
    target_idausr.mkdir()
    (target_idausr / "cfg").mkdir()

    ida_reg = source_idausr / "ida.reg"
    if not ida_reg.exists():
        pytest.skip("Current IDAUSR does not contain ida.reg")
    (target_idausr / "ida.reg").write_bytes(ida_reg.read_bytes())

    for license_file in source_idausr.glob("*.hexlic"):
        (target_idausr / license_file.name).write_bytes(license_file.read_bytes())


def test_is_externally_managed_true_for_managed_base_install():
    probe = _ida_info((3, 11), externally_managed=True)
    resolved = ResolvedPython(Path("/opt/python3/bin/python3"), "derived from idat probe", probe)

    assert is_externally_managed(resolved)


def test_is_externally_managed_false_when_marker_absent():
    probe = _ida_info((3, 11), externally_managed=False)
    resolved = ResolvedPython(Path("/opt/python3/bin/python3"), "derived from idat probe", probe)

    assert not is_externally_managed(resolved)


def test_is_externally_managed_false_for_venv_even_when_base_is_managed(tmp_path):
    """A venv on top of a managed install is exempt, matching pip's own PEP 668 behavior."""
    venv_dir = tmp_path / "venv"
    python = _write_fake_venv(venv_dir, "3.11.0")
    probe = _ida_info((3, 11), externally_managed=True)
    resolved = ResolvedPython(python, "derived from idat probe", probe)

    assert not is_externally_managed(resolved)


def test_is_externally_managed_false_without_probe():
    resolved = ResolvedPython(Path("/opt/python3/bin/python3"), "$HCLI_CURRENT_IDA_PYTHON_EXE", None)

    assert not is_externally_managed(resolved)


def _assert_detected_venv_python(result: Path, venv_dir: Path) -> None:
    if os.name == "nt":
        assert result == venv_dir / "Scripts" / "python.exe"
        return

    assert result.parent == venv_dir / "bin"
    assert result.name.startswith("python")


def _create_venv_with_ida_python(venv_dir: Path) -> None:
    """Build the venv using IDA's own Python so the venv is one IDA could plausibly use.

    Otherwise the venv's interpreter version may not match IDA's embedded Python
    (e.g. uv-managed test runner is 3.10 but IDA ships 3.13).
    """
    ida_python = find_current_python_executable()
    subprocess.run([str(ida_python), "-m", "venv", str(venv_dir)], check=True)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_returns_path():
    """Test that find_current_python_executable returns a valid path."""
    result = find_current_python_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "python" in result.name.lower()


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_has_pip():
    python_exe = find_current_python_executable()
    assert has_pip(python_exe, timeout=30.0)


def test_resolve_current_python_prefers_hcli_env_pin(tmp_path, monkeypatch):
    """$HCLI_CURRENT_IDA_PYTHON_EXE wins, and skips the (slow) idat probe entirely."""
    hcli_python = tmp_path / "hcli" / "python3"
    hcli_python.parent.mkdir()
    hcli_python.write_text("", encoding="utf-8")

    venv_python = tmp_path / "venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE", str(hcli_python))
    set_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE", str(venv_python))

    resolved = resolve_current_python()

    assert resolved.exe == hcli_python
    assert resolved.probe is None


def test_find_current_python_executable_uses_idapython_venv_executable(tmp_path, monkeypatch):
    python = tmp_path / "bin" / "python3"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")

    unset_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE")
    set_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE", str(python))

    result = find_current_python_executable()
    assert result == python


def test_derive_python_exe_uses_idapython_venv_executable_when_sys_executable_is_idat(tmp_path):
    """IDA 9.4 macOS: sys.executable is the idat binary, not a Python interpreter.

    When prefix==base_prefix (macOS framework) and sys.executable is not a Python
    path, _derive_python_exe must still honour IDAPYTHON_VENV_EXECUTABLE.
    """
    framework = "/Library/Frameworks/Python.framework/Versions/3.14"
    venv_python = _write_fake_venv(tmp_path / "venv", "3.14.0")

    # Simulate a fake idat binary as sys.executable
    idat_binary = tmp_path / "idat"
    idat_binary.write_text("", encoding="utf-8")

    info = _ida_info(
        (3, 14),
        prefix=framework,
        base_prefix=framework,
        executable=str(idat_binary),
        idapython_venv_executable=str(venv_python),
    )

    assert _derive_python_exe(info) == venv_python


def test_derive_python_exe_honors_validated_virtualenv_executable_when_prefix_is_base(tmp_path):
    framework = "/Library/Frameworks/Python.framework/Versions/3.14"
    venv_dir = tmp_path / "venv"
    venv_python = _write_fake_venv(venv_dir, "3.14.0")

    info = _ida_info(
        (3, 14),
        prefix=framework,
        base_prefix=framework,
        executable=str(venv_python),
        virtual_env=str(venv_dir),
        idapython_venv_executable=str(venv_python),
    )

    assert _derive_python_exe(info) == venv_python


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_activated_virtualenv(tmp_path, monkeypatch):
    """VIRTUAL_ENV in the hcli process env is stripped before invoking idat,
    so the only way to detect a venv is via idapythonrc.py activating it inside idat.
    This test verifies that an idapythonrc.py that sets sys.prefix to a venv
    causes find_current_python_executable to return the venv's Python.
    """
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    _create_venv_with_ida_python(venv_dir)

    target_idausr = tmp_path / "idausr-activated"
    _prepare_isolated_idausr_for_python_detection(source_idausr, target_idausr)

    (target_idausr / "idapythonrc.py").write_text(
        "import os, sys\nvenv = os.environ['HCLI_TEST_VENV']\nos.environ['VIRTUAL_ENV'] = venv\nsys.prefix = venv\n",
        encoding="utf-8",
    )

    set_env_var(monkeypatch, "HCLI_IDAUSR", str(target_idausr))
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    set_env_var(monkeypatch, "HCLI_TEST_VENV", str(venv_dir))
    unset_env_var(monkeypatch, "VIRTUAL_ENV")
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")
    unset_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE")

    # building the venv above probed IDA under the ambient environment;
    # this test changes the environment, so it needs a fresh probe.
    probe_current_python_info.cache_clear()

    result = find_current_python_executable()
    _assert_detected_venv_python(result, venv_dir)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_idapython_venv_executable(tmp_path, monkeypatch):
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    _create_venv_with_ida_python(venv_dir)

    target_idausr = tmp_path / "idausr-venv-executable"
    _prepare_isolated_idausr_for_python_detection(source_idausr, target_idausr)

    set_env_var(monkeypatch, "HCLI_IDAUSR", str(target_idausr))
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    set_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE", str(_venv_launcher_for_ida(venv_dir)))
    unset_env_var(monkeypatch, "VIRTUAL_ENV")
    unset_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE")

    result = find_current_python_executable()
    _assert_detected_venv_python(result, venv_dir)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_python_executable_honors_idapythonrc(tmp_path, monkeypatch):
    source_idausr = get_ida_user_dir()
    if not source_idausr.exists():
        pytest.skip("Current IDAUSR directory not available")

    install_dir = find_current_ida_install_directory()
    venv_dir = tmp_path / "venv"
    _create_venv_with_ida_python(venv_dir)

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

    set_env_var(monkeypatch, "HCLI_IDAUSR", str(target_idausr))
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR", str(install_dir))
    set_env_var(monkeypatch, "HCLI_TEST_VENV", str(venv_dir))
    unset_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE")

    # building the venv above probed IDA under the ambient environment;
    # this test changes the environment, so it needs a fresh probe.
    probe_current_python_info.cache_clear()

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


def _fake_pip_result(returncode: int, stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr.encode())


EXTERNALLY_MANAGED_STDERR = (
    "error: externally-managed-environment\n\n"
    "× This environment is externally managed\n"
    "╰─> To install Python packages system-wide, try brew install xyz.\n"
)


def test_verify_pip_can_install_packages_explains_externally_managed_environment(tmp_path, monkeypatch):
    python_exe = tmp_path / "python3"
    monkeypatch.setattr(
        "hcli.lib.ida.python.subprocess.run",
        lambda *a, **k: _fake_pip_result(1, EXTERNALLY_MANAGED_STDERR),
    )

    with pytest.raises(CantInstallPackagesError) as exc_info:
        verify_pip_can_install_packages(python_exe, ["ida-domain"])

    message = str(exc_info.value)
    assert "PEP 668" in message
    assert "explain-environment" in message
    assert str(python_exe) in message


def test_pip_install_packages_explains_externally_managed_environment(tmp_path, monkeypatch):
    python_exe = tmp_path / "python3"
    monkeypatch.setattr(
        "hcli.lib.ida.python.subprocess.run",
        lambda *a, **k: _fake_pip_result(1, EXTERNALLY_MANAGED_STDERR),
    )

    with pytest.raises(CantInstallPackagesError) as exc_info:
        pip_install_packages(python_exe, ["ida-domain"])

    assert "PEP 668" in str(exc_info.value)


@pytest.mark.parametrize(
    "options,expected",
    [
        (PipOptions(), []),
        (
            PipOptions(
                index_url="https://pypi.example.corp/simple",
                extra_index_urls=("https://a.example.com/simple", "https://b.example.com/simple"),
                find_links=("/local/wheels",),
            ),
            [
                "--index-url",
                "https://pypi.example.corp/simple",
                "--extra-index-url",
                "https://a.example.com/simple",
                "--extra-index-url",
                "https://b.example.com/simple",
                "--find-links",
                "/local/wheels",
            ],
        ),
        (
            PipOptions(
                offline=True,
                isolated=True,
                no_cache_dir=True,
                disable_pip_version_check=True,
                find_links=("/tmp/wheelhouse",),
            ),
            [
                "--isolated",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-index",
                "--find-links",
                "/tmp/wheelhouse",
            ],
        ),
        (PipOptions(no_build_isolation=True), ["--no-build-isolation"]),
    ],
)
def test_pip_options_build_args(options: PipOptions, expected: list[str]):
    assert options.build_args() == expected


@pytest.mark.parametrize(
    "options,expected",
    [
        (PipOptions(), False),
        # offline alone isn't a source: it only forbids the default index
        (PipOptions(offline=True), False),
        (PipOptions(index_url="https://example.com"), True),
        (PipOptions(extra_index_urls=("https://example.com",)), True),
        (PipOptions(find_links=("/tmp/wh",)), True),
    ],
)
def test_pip_options_has_custom_sources(options: PipOptions, expected: bool):
    assert options.has_custom_sources is expected


def test_merge_bundle_pip_options_unions_user_and_bundle():
    user = PipOptions(offline=True, no_build_isolation=True, find_links=("/user/wh",))
    bundle = PipOptions(
        isolated=True,
        no_cache_dir=True,
        disable_pip_version_check=True,
        find_links=("/bundle/wh",),
    )

    merged = merge_bundle_pip_options(user, bundle)

    assert merged == PipOptions(
        find_links=("/bundle/wh", "/user/wh"),
        offline=True,
        isolated=True,
        no_cache_dir=True,
        disable_pip_version_check=True,
        no_build_isolation=True,
    )


def test_merge_bundle_pip_options_default_user():
    bundle = PipOptions(
        offline=True,
        isolated=True,
        no_cache_dir=True,
        disable_pip_version_check=True,
        find_links=("/tmp/wh",),
    )
    assert merge_bundle_pip_options(PipOptions(), bundle) == bundle


def test_get_virtual_env_version_falls_back_to_pyvenv_cfg(tmp_path):
    venv = tmp_path / "venv"
    _write_fake_venv(venv, "3.14.1")

    assert get_virtual_env_version(venv) == "3.14"


@pytest.mark.skipif(os.name == "nt", reason="symlinking an interpreter requires privileges on Windows")
def test_get_virtual_env_version_prefers_the_interpreter_over_pyvenv_cfg(tmp_path):
    venv = tmp_path / "venv"
    _write_fake_venv(venv, "9.9.9")

    # replace the unrunnable stub with the interpreter running this test,
    # so probing succeeds and disagrees with the stale pyvenv.cfg
    python = _venv_launcher_for_ida(venv)
    python.unlink()
    python.symlink_to(sys.executable)

    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert get_virtual_env_version(venv) == expected


def test_get_virtual_env_version_returns_none_when_unknown(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()

    assert get_virtual_env_version(venv) is None


def test_find_python_version_mismatches_detects_ida_venv_mismatch(tmp_path):
    """idapyswitch registered 3.12, but idapythonrc.py activates a 3.14 venv."""
    venv = tmp_path / "venv"
    venv_python = _write_fake_venv(venv, "3.14.1")

    info = _ida_info((3, 12), virtual_env=str(venv), executable=str(venv_python))

    mismatches = find_python_version_mismatches(info, venv_python)

    assert len(mismatches) == 1
    assert mismatches[0].ida_version == "3.12"
    assert mismatches[0].other_version == "3.14"
    assert mismatches[0].other_path == venv
    assert "$VIRTUAL_ENV" in mismatches[0].other_source


def test_find_python_version_mismatches_accepts_matching_versions(tmp_path):
    venv = tmp_path / "venv"
    venv_python = _write_fake_venv(venv, "3.12.7")

    info = _ida_info((3, 12), virtual_env=str(venv), executable=str(venv_python))

    assert find_python_version_mismatches(info, venv_python) == []


def test_find_python_version_mismatches_detects_requested_venv_mismatch(tmp_path):
    """IDAPYTHON_VENV_EXECUTABLE points at a venv IDA's interpreter can't use."""
    venv = tmp_path / "venv"
    venv_python = _write_fake_venv(venv, "3.14.1")

    info = _ida_info((3, 12), idapython_venv_executable=str(venv_python))

    mismatches = find_python_version_mismatches(info, venv_python)

    assert len(mismatches) == 1
    assert mismatches[0].other_version == "3.14"
    assert mismatches[0].other_path == venv
    assert "$IDAPYTHON_VENV_EXECUTABLE" in mismatches[0].other_source


def test_find_python_version_mismatches_reports_each_venv_once(tmp_path):
    """The same venv reached two ways is one problem, not two."""
    venv = tmp_path / "venv"
    venv_python = _write_fake_venv(venv, "3.14.1")

    info = _ida_info(
        (3, 12),
        virtual_env=str(venv),
        idapython_venv_executable=str(venv_python),
        executable=str(venv_python),
    )

    assert len(find_python_version_mismatches(info, venv_python)) == 1


def test_find_python_version_mismatches_detects_install_interpreter_mismatch():
    """The interpreter hcli would install into disagrees with IDA's embedded Python."""
    # sys.executable is real, so its version is probed rather than guessed
    info = _ida_info((3, 1), executable=sys.executable)

    mismatches = find_python_version_mismatches(info, Path(sys.executable))

    assert len(mismatches) == 1
    assert mismatches[0].ida_version == "3.1"
    assert mismatches[0].other_version == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert mismatches[0].other_path == Path(sys.executable)
    assert "install" in mismatches[0].other_source


def test_find_python_version_mismatches_ignores_unreadable_venv(tmp_path):
    """A venv whose version can't be determined isn't reported as a mismatch."""
    venv = tmp_path / "venv"
    venv.mkdir()

    info = _ida_info((3, 12), virtual_env=str(venv), executable=str(sys.executable))

    mismatches = find_python_version_mismatches(info, Path(sys.executable))

    assert all(m.other_path != venv for m in mismatches)


def test_probe_current_python_info_runs_idat_once_per_process(monkeypatch):
    """The idat probe takes seconds, so repeated resolutions must reuse one probe."""
    calls = []

    def fake_probe(src: str) -> dict:
        calls.append(src)
        return _ida_info((3, 12)).model_dump()

    monkeypatch.setattr("hcli.lib.ida.python.run_py_in_current_idapython", fake_probe)

    first = probe_current_python_info()
    second = probe_current_python_info()

    assert first == second == _ida_info((3, 12))
    assert len(calls) == 1


def test_probe_python_version_returns_none_for_unrunnable_interpreter(tmp_path):
    fake = tmp_path / "not-python"
    fake.write_text("", encoding="utf-8")

    assert probe_python_version(fake) is None


def test_format_python_version_mismatch_warning_is_empty_without_mismatches():
    assert format_python_version_mismatch_warning([]) == ""
