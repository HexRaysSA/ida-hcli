import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pytest
import rich.status
from fixtures import *
from fixtures import (
    PLUGINS_DIR,
    install_this_package_in_venv,
    run_hcli,
    temp_env_var,
)

from hcli.lib.console import stderr_console
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    DependencyInstallationError,
    PluginAlreadyInstalledError,
    PluginInUseError,
    PluginNotInstalledError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.install import (
    extract_zip_subdirectory_to,
    get_installed_plugin_records,
    get_plugin_directory,
    get_trash_directory,
    install_plugin_archive,
    is_plugin_installed,
    is_vcs_or_url_dependency,
    sweep_trash,
    uninstall_plugin,
    upgrade_plugin_archive,
    validate_archive_entry,
)
from hcli.lib.ida.python import IdatProbe, ResolvedPython

logger = logging.getLogger(__name__)


def get_installed_plugins() -> list[tuple[str, str]]:
    """(name, version) pairs for the currently installed plugins"""
    return [(r.name, r.version) for r in get_installed_plugin_records()]


def pip_freeze(python_exe: Path) -> str:
    """Distributions installed in the given interpreter, as `pip freeze` output."""
    argv = [str(python_exe), "-m", "pip", "freeze"]
    process = subprocess.run(argv, capture_output=True, check=True)
    return process.stdout.decode("utf-8", errors="replace")


def row_contains(*values: str):
    """Return a matcher function that checks if a line contains all values."""

    def matcher(output: str) -> bool:
        for line in output.splitlines():
            normalized = " ".join(line.split())
            if all(v in normalized for v in values):
                return True
        return False

    return matcher


def test_install_source_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_install_source_plugin_archive_under_an_active_spinner(virtual_ida_environment):
    """`hcli plugin install` wraps the install in its own spinner, and the install
    starts more of its own. rich before 14.1 aborted the command there with
    `Only one live display may be active at once`.
    """
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    with rich.status.Status("installing plugin", console=stderr_console):
        install_plugin_archive(buf, "plugin1")

    assert is_plugin_installed("plugin1")


def test_install_binary_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "zydisinfo")

    plugin_directory = get_plugin_directory("zydisinfo")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "zydisinfo.dll").exists()
    assert (plugin_directory / "zydisinfo.so").exists()
    assert (plugin_directory / "zydisinfo.dylib").exists()

    assert ("zydisinfo", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("zydisinfo")


def test_uninstall(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    uninstall_plugin("plugin1")
    assert ("plugin1", "1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("plugin1")
    assert not get_plugin_directory("plugin1").exists()


def test_upgrade(virtual_ida_environment):
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    install_plugin_archive(v1, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    upgrade_plugin_archive(v2, "plugin1")
    assert ("plugin1", "2.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    uninstall_plugin("plugin1")

    install_plugin_archive(v2, "plugin1")
    with pytest.raises(PluginVersionDowngradeError):
        # this is a downgrade
        upgrade_plugin_archive(v1, "plugin1")


def test_plugin_python_dependencies(virtual_ida_environment_with_venv):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    freeze = pip_freeze(Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]))
    assert "packaging==25.0" in freeze


@pytest.mark.parametrize(
    "dependency,expected",
    [
        ("packaging==25.0", False),
        ("packaging>=1.0,<3", False),
        ("git+https://github.com/HexRaysSA/speakeasy.git@gdb-improvements", True),
        ("hg+https://example.com/repo@tip", True),
        ("speakeasy @ git+https://github.com/HexRaysSA/speakeasy.git@gdb-improvements", True),
        ("some-pkg @ https://example.com/some-pkg-1.0.whl", True),
        ("https://example.com/some-pkg-1.0.whl", True),
    ],
)
def test_is_vcs_or_url_dependency(dependency: str, expected: bool):
    assert is_vcs_or_url_dependency(dependency) is expected


def test_installing_a_plugin_does_not_refetch_another_plugins_vcs_dependency(
    virtual_ida_environment_with_venv, monkeypatch
):
    """Merging in other installed plugins' deps is for conflict detection only.

    VCS/URL deps can't be cheaply checked for "already satisfied" (e.g. a git
    branch ref has no stable version), so pip re-fetches them - clone,
    submodules and all - on every unrelated plugin install unless we exclude
    them from the merge.
    """
    existing_plugin_dir = get_plugin_directory("existing-plugin")
    existing_plugin_dir.mkdir(parents=True)
    (existing_plugin_dir / "existing_plugin.py").write_text("# plugin code")
    (existing_plugin_dir / "ida-plugin.json").write_text(
        json.dumps(
            {
                "IDAMetadataDescriptorVersion": 1,
                "plugin": {
                    "name": "existing-plugin",
                    "version": "1.0.0",
                    "entryPoint": "existing_plugin.py",
                    "pythonDependencies": ["git+https://github.com/HexRaysSA/speakeasy.git@gdb-improvements"],
                },
            }
        )
    )

    installed_packages: list[list[str]] = []
    monkeypatch.setattr(
        "hcli.lib.ida.plugin.install.verify_pip_can_install_packages",
        lambda python_exe, packages, pip_options=None: installed_packages.append(list(packages)),
    )
    monkeypatch.setattr(
        "hcli.lib.ida.plugin.install.pip_install_packages",
        lambda python_exe, packages, pip_options=None: installed_packages.append(list(packages)),
    )

    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()
    install_plugin_archive(buf, "plugin1")

    for packages in installed_packages:
        assert "packaging==25.0" in packages
        assert not any(is_vcs_or_url_dependency(p) for p in packages)


def test_plugin_python_dependencies_rejects_externally_managed_python_without_running_pip(
    virtual_ida_environment, monkeypatch
):
    """PEP 668 detection must short-circuit before any pip subprocess runs."""
    fake_python = Path("/opt/homebrew/bin/python3")
    probe = IdatProbe(
        prefix="/opt/homebrew",
        base_prefix="/opt/homebrew",
        executable=str(fake_python),
        version_major=3,
        version_minor=13,
        externally_managed=True,
    )
    resolved = ResolvedPython(fake_python, "derived from idat probe", probe)

    def _must_not_run_pip(*args, **kwargs):
        raise AssertionError("pip should never be invoked once PEP 668 is detected")

    monkeypatch.setattr("hcli.lib.ida.plugin.install.resolve_current_python", lambda: resolved)
    monkeypatch.setattr("hcli.lib.ida.plugin.install.has_pip", _must_not_run_pip)
    monkeypatch.setattr("hcli.lib.ida.plugin.install.verify_pip_can_install_packages", _must_not_run_pip)
    monkeypatch.setattr("hcli.lib.ida.plugin.install.pip_install_packages", _must_not_run_pip)

    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()

    with pytest.raises(DependencyInstallationError) as exc_info:
        install_plugin_archive(buf, "plugin1")

    assert "PEP 668" in str(exc_info.value)
    assert not is_plugin_installed("plugin1")


def test_plugin_all(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"), temp_env_var("COLUMNS", "80"):
        p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} repo snapshot")
        assert "plugin1" in p.stdout
        assert "zydisinfo" in p.stdout
        assert "1.0.0" in p.stdout
        assert "4.0.0" in p.stdout
        # ensure it looks like json
        _ = json.loads(p.stdout)

        repo_path = idausr / "repo.json"
        repo_path.write_text(p.stdout, encoding="utf-8")

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert "No plugins found\n" == p.stdout

        # current platform: macos-aarch64
        # current version: 9.1
        #
        # plugin1    4.0.0    https://github.com/HexRaysSA/ida-hcli
        # zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli
        p = run_hcli(f"plugin --repo {repo_path.absolute()} search")
        assert row_contains("plugin1", "6.0.0", "https://github.com/HexRaysSA/ida-hcli")(p.stdout)
        assert row_contains("zydisinfo", "1.0.0", "https://github.com/HexRaysSA/ida-hcli")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydis")
        assert row_contains("zydisinfo", "1.0.0", "https://github.com/HexRaysSA/ida-hcli")(p.stdout)
        assert not row_contains("plugin1", "6.0.0")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo")
        assert "name: zydisinfo" in p.stdout
        assert "available versions:\n 1.0.0" in p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo==1.0.0")
        assert "name: zydisinfo" in p.stdout
        assert "download locations:\n" in p.stdout
        assert "IDA: 9.0-9.2  platforms: all" in p.stdout
        assert "file://" in p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} install zydisinfo")
        assert "Installed plugin: zydisinfo==1.0.0\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert row_contains("zydisinfo", "1.0.0")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall zydisinfo")
        assert "Uninstalled plugin: zydisinfo\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert "No plugins found\n" == p.stdout

        # uppercase name: the repo lookup is case-insensitive and reports the canonical name
        p = run_hcli(f"plugin --repo {repo_path.absolute()} install PLUGIN1==1.0.0")
        assert "Installed plugin: plugin1==1.0.0\n" == p.stdout
        assert ("plugin1", "1.0.0") in get_installed_plugins()

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert row_contains("plugin1", "1.0.0", "upgradable to 6.0.0")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==2.0.0")
        assert "Installed plugin: plugin1==2.0.0\n" == p.stdout

        # downgrade not supported
        with pytest.raises(subprocess.CalledProcessError) as e:
            run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==1.0.0")
        # rich wraps the message to COLUMNS, so compare against unwrapped output
        assert "new version 1.0.0 is not greater than current version 2.0.0" in " ".join(e.value.stdout.split())

        # TODO: upgrade all

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert row_contains("plugin1", "2.0.0", "upgradable to 6.0.0")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
        assert "Uninstalled plugin: plugin1\n" == p.stdout

        p = run_hcli(
            f"plugin --repo {repo_path.absolute()} install {(PLUGINS_DIR / 'plugin1' / 'plugin1-v3.0.0.zip').absolute()}"
        )
        assert "Installed plugin: plugin1==3.0.0\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
        assert "Uninstalled plugin: plugin1\n" == p.stdout

        # install from file:// path URI
        p = run_hcli(
            f"plugin --repo {repo_path.absolute()} install {(PLUGINS_DIR / 'plugin1' / 'plugin1-v4.0.0.zip').absolute().as_uri()}"
        )
        assert "Installed plugin: plugin1==4.0.0\n" == p.stdout

        # TODO: install by URL
        # which will require a plugin archive with a single plugin

        # work with the default index
        # if `hint-calls` becomes unmaintained, this plugin name can be changed.
        # the point is just to show the default index works.
        p = run_hcli("plugin search hint-ca")
        assert "hint-calls" in p.stdout

        p = run_hcli("plugin install hint-calls")
        assert "Installed plugin: hint-calls==" in p.stdout


def test_extract_zip_subdirectory_to_posix_paths():
    """
    Test that extract_zip_subdirectory_to works with forward-slash paths.

    ZIP files always use forward slashes internally (per ZIP specification).
    On Windows, Path objects convert to backslashes when str() is called,
    which would break path matching. This test verifies the fix using .as_posix().
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/plugin/ida-plugin.json", '{"test": true}')
        zf.writestr("repo-main/plugin/plugin.py", "# plugin code")
        zf.writestr("repo-main/plugin/subdir/helper.py", "# helper code")
    zip_data = buf.getvalue()

    subdirectory = Path("repo-main/plugin")

    with tempfile.TemporaryDirectory() as temp_dir:
        # nested like $IDAUSR/plugins/<name> so staging lands within temp_dir
        destination = Path(temp_dir) / "plugins" / "myplugin"
        destination.parent.mkdir()
        extract_zip_subdirectory_to(zip_data, subdirectory, destination)

        assert destination.exists()
        assert (destination / "ida-plugin.json").exists()
        assert (destination / "plugin.py").exists()
        assert (destination / "subdir" / "helper.py").exists()


@pytest.mark.parametrize(
    "filename,relative_path,external_attr,message",
    [
        ("plugin/../../../etc/passwd", "../../../etc/passwd", 0, "Path traversal"),
        # 0xA in the high nibble of external_attr marks a Unix symlink
        ("plugin/evil_symlink", "evil_symlink", 0xA0000000, "Symlinks not allowed"),
        ("/etc/passwd", "/etc/passwd", 0, "Absolute path"),
    ],
)
def test_validate_archive_entry_rejects_unsafe_entries(
    filename: str, relative_path: str, external_attr: int, message: str
):
    file_info = zipfile.ZipInfo(filename)
    file_info.external_attr = external_attr

    with pytest.raises(ValueError, match=message):
        validate_archive_entry(file_info, PurePosixPath(relative_path))


def test_install_already_installed(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()

    install_plugin_archive(buf, "plugin1")
    with pytest.raises(PluginAlreadyInstalledError):
        install_plugin_archive(buf, "plugin1")


def test_uninstall_not_installed(virtual_ida_environment):
    with pytest.raises(PluginNotInstalledError):
        uninstall_plugin("plugin1")


def break_installed_plugin(name: str) -> Path:
    """Simulate an interrupted uninstall (issue #228): the manifest is gone
    but other plugin files remain, so the directory is not a valid
    installation yet still blocks the name.
    """
    plugin_dir = get_plugin_directory(name)
    (plugin_dir / "ida-plugin.json").unlink()
    return plugin_dir


def test_uninstall_broken_plugin_directory(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    plugin_dir = break_installed_plugin("plugin1")
    assert not is_plugin_installed("plugin1")

    # must remove the remnants rather than raise PluginNotInstalledError
    uninstall_plugin("plugin1")
    assert not plugin_dir.exists()


def test_install_over_broken_plugin_directory(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    break_installed_plugin("plugin1")

    # install must distinguish remnants from a working installation,
    # and the suggested recovery (uninstall, then install) must work
    with pytest.raises(BrokenPluginInstallationError):
        install_plugin_archive(buf, "plugin1")

    uninstall_plugin("plugin1")
    install_plugin_archive(buf, "plugin1")
    assert is_plugin_installed("plugin1")


def test_uninstall_file_squatting_on_plugin_name(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    squatter = get_plugin_directory("plugin1")
    squatter.write_text("not a plugin")

    with pytest.raises(BrokenPluginInstallationError):
        install_plugin_archive(buf, "plugin1")

    # the suggested recovery (uninstall, then install) must work for files too
    uninstall_plugin("plugin1")
    assert not squatter.exists()

    install_plugin_archive(buf, "plugin1")
    assert is_plugin_installed("plugin1")


def test_trash_directory_not_scanned(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    # a trashed copy retains its manifest but must not count as installed
    trash_dir = get_trash_directory()
    trash_dir.mkdir(exist_ok=True)
    os.rename(get_plugin_directory("plugin1"), trash_dir / "plugin1-cafe0123")

    assert not is_plugin_installed("plugin1")
    assert get_installed_plugins() == []


def test_sweep_trash(virtual_ida_environment):
    trash_dir = get_trash_directory()
    trash_dir.mkdir(exist_ok=True)
    (trash_dir / "plugin1-deadbeef").mkdir()
    (trash_dir / "plugin1-deadbeef" / "plugin1.py").write_text("# leftover")
    (trash_dir / "plugin2.staging-deadbeef").mkdir()

    sweep_trash()
    assert list(trash_dir.iterdir()) == []


def test_sweep_trash_without_trash_directory(virtual_ida_environment):
    assert not get_trash_directory().exists()
    sweep_trash()


def append_path_traversal_entry(zip_data: bytes, prefix: str) -> bytes:
    """Append a malicious entry so extraction fails after validation of the
    metadata has already passed -- forcing failure mid-upgrade.
    """
    buf = io.BytesIO(zip_data)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(f"{prefix}/../evil.txt", "malicious content")
    return buf.getvalue()


def test_upgrade_failure_rolls_back(virtual_ida_environment):
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    install_plugin_archive(v1, "plugin1")

    corrupt_v2 = append_path_traversal_entry(v2, "src-v2")
    with pytest.raises(ValueError, match="Path traversal"):
        upgrade_plugin_archive(corrupt_v2, "plugin1")

    # the failed upgrade must restore the previous version
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    # and must not leave state that blocks a later, good upgrade
    upgrade_plugin_archive(v2, "plugin1")
    assert ("plugin1", "2.0.0") in get_installed_plugins()


def test_failed_extraction_leaves_no_partial_destination():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin/ida-plugin.json", '{"test": true}')
        zf.writestr("plugin/../../../tmp/evil.txt", "malicious content")
    zip_data = buf.getvalue()

    with tempfile.TemporaryDirectory() as temp_dir:
        # nested like $IDAUSR/plugins/<name> so staging lands within temp_dir
        destination = Path(temp_dir) / "plugins" / "myplugin"
        destination.parent.mkdir()
        with pytest.raises(ValueError, match="Path traversal"):
            extract_zip_subdirectory_to(zip_data, Path("plugin"), destination)

        assert not destination.exists()
        assert list(get_trash_directory(destination.parent).iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="file locking semantics are Windows-specific")
def test_uninstall_while_file_in_use_is_atomic(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    plugin_dir = get_plugin_directory("plugin1")

    # Python opens files without FILE_SHARE_DELETE, so while this handle is
    # held, renaming the plugin directory fails just like when a running IDA
    # has the plugin loaded.
    with (plugin_dir / "plugin1.py").open("rb"):
        with pytest.raises(PluginInUseError):
            uninstall_plugin("plugin1")

        # nothing was modified: still a fully valid installation
        assert is_plugin_installed("plugin1")
        assert (plugin_dir / "ida-plugin.json").exists()

    uninstall_plugin("plugin1")
    assert not is_plugin_installed("plugin1")
