import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import (
    PLUGINS_DIR,
    install_this_package_in_venv,
    run_hcli,
    temp_env_var,
)

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin import ALL_PLATFORMS
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    DependencyInstallationError,
    IDAVersionIncompatibleError,
    PlatformIncompatibleError,
    PluginAlreadyInstalledError,
    PluginInUseError,
    PluginNotInstalledError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.install import (
    extract_zip_subdirectory_to,
    get_installed_plugins,
    get_plugin_directory,
    get_trash_directory,
    install_plugin_archive,
    is_plugin_installed,
    sweep_trash,
    uninstall_plugin,
    upgrade_plugin_archive,
    validate_archive_entry,
)
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo
from hcli.lib.ida.python import CantInstallPackagesError, pip_freeze

logger = logging.getLogger(__name__)


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
    assert not is_plugin_installed("zydisinfo")


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


def test_plugin_all(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"), temp_env_var("COLUMNS", "80"):
        p = run_hcli("--help")
        assert "Usage: python -m hcli.main [OPTIONS] COMMAND [ARGS]..." in p.stdout

        p = run_hcli("plugin --help")
        assert "Usage: python -m hcli.main plugin [OPTIONS] COMMAND [ARGS]..." in p.stdout

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

        p = run_hcli(f"plugin --repo {repo_path.absolute()} install plugin1==1.0.0")
        assert "Installed plugin: plugin1==1.0.0\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert row_contains("plugin1", "1.0.0", "upgradable to 6.0.0")(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==2.0.0")
        assert "Installed plugin: plugin1==2.0.0\n" == p.stdout

        # downgrade not supported
        with pytest.raises(subprocess.CalledProcessError) as e:
            p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==1.0.0")
            assert (
                e.value.stdout
                == "Error: Cannot upgrade plugin plugin1: new version 1.0.0 is not greater than existing version 2.0.0\n"
            )

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


def test_case_insensitive_plugin_install(virtual_ida_environment_with_venv):
    """Test that plugin install works with case-insensitive name matching."""
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"), temp_env_var("COLUMNS", "80"):
        p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} repo snapshot")
        repo_path = idausr / "repo.json"
        repo_path.write_text(p.stdout, encoding="utf-8")

        # Install using uppercase name "PLUGIN1" but expect it to resolve to "plugin1"
        p = run_hcli(f"plugin --repo {repo_path.absolute()} install PLUGIN1==1.0.0")
        assert "Installed plugin: plugin1==1.0.0\n" == p.stdout

        # Verify the plugin is installed with the correct case
        assert is_plugin_installed("plugin1")
        assert ("plugin1", "1.0.0") in get_installed_plugins()

        # Clean up
        p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
        assert "Uninstalled plugin: plugin1\n" == p.stdout


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


class TestValidateArchiveEntry:
    """Tests for validate_archive_entry security function (CVE fix for path traversal)."""

    def test_valid_entry_passes(self):
        """Normal archive entries should pass validation."""
        import pathlib

        file_info = zipfile.ZipInfo("plugin/file.py")
        file_info.external_attr = 0  # Regular file
        relative_path = pathlib.PurePosixPath("file.py")

        # Should not raise
        validate_archive_entry(file_info, relative_path)

    def test_rejects_path_traversal(self):
        """Entries with '..' path components should be rejected."""
        import pathlib

        file_info = zipfile.ZipInfo("plugin/../../../etc/passwd")
        file_info.external_attr = 0
        relative_path = pathlib.PurePosixPath("../../../etc/passwd")

        with pytest.raises(ValueError, match="Path traversal"):
            validate_archive_entry(file_info, relative_path)

    def test_rejects_symlinks(self):
        """Symlinks in archives should be rejected."""
        import pathlib

        file_info = zipfile.ZipInfo("plugin/evil_symlink")
        # Set external_attr to indicate Unix symlink (0xA in high nibble)
        file_info.external_attr = 0xA0000000
        relative_path = pathlib.PurePosixPath("evil_symlink")

        with pytest.raises(ValueError, match="Symlinks not allowed"):
            validate_archive_entry(file_info, relative_path)

    def test_rejects_absolute_paths(self):
        """Absolute paths should be rejected."""
        import pathlib

        file_info = zipfile.ZipInfo("/etc/passwd")
        file_info.external_attr = 0
        relative_path = pathlib.PurePosixPath("/etc/passwd")

        with pytest.raises(ValueError, match="Absolute path"):
            validate_archive_entry(file_info, relative_path)

    def test_extract_rejects_malicious_archive(self):
        """Integration test: extract_zip_subdirectory_to should reject path traversal."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("plugin/ida-plugin.json", '{"test": true}')
            zf.writestr("plugin/../../../tmp/evil.txt", "malicious content")
        zip_data = buf.getvalue()

        subdirectory = Path("plugin")

        with tempfile.TemporaryDirectory() as temp_dir:
            # nested like $IDAUSR/plugins/<name> so staging lands within temp_dir
            destination = Path(temp_dir) / "plugins" / "myplugin"
            destination.parent.mkdir()
            with pytest.raises(ValueError, match="Path traversal"):
                extract_zip_subdirectory_to(zip_data, subdirectory, destination)


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


def rewrite_plugin_metadata(zip_data: bytes, **fields) -> bytes:
    """Return a copy of a plugin archive with `plugin` metadata fields replaced.

    Lets a test synthesize an archive that doesn't support the current
    environment without shipping another fixture zip.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_data)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith("ida-plugin.json"):
                doc = json.loads(data)
                doc["plugin"].update(fields)
                data = json.dumps(doc, indent=2).encode("utf-8")
            dst.writestr(item, data)
    return buf.getvalue()


def get_foreign_platform() -> str:
    """A platform that isn't the one the test environment claims to run."""
    current = os.environ["HCLI_CURRENT_IDA_PLATFORM"]
    return next(platform for platform in sorted(ALL_PLATFORMS) if platform != current)


def test_install_incompatible_ida_version(virtual_ida_environment):
    # the fixture environment reports IDA 9.1
    buf = rewrite_plugin_metadata((PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes(), idaVersions=["9.0"])

    with pytest.raises(IDAVersionIncompatibleError):
        install_plugin_archive(buf, "plugin1")
    assert not is_plugin_installed("plugin1")

    install_plugin_archive(buf, "plugin1", allow_incompatible=True)
    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_install_incompatible_platform(virtual_ida_environment):
    buf = rewrite_plugin_metadata(
        (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes(), platforms=[get_foreign_platform()]
    )

    with pytest.raises(PlatformIncompatibleError):
        install_plugin_archive(buf, "plugin1")
    assert not is_plugin_installed("plugin1")

    install_plugin_archive(buf, "plugin1", allow_incompatible=True)
    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_install_compatible_plugin_is_unaffected_by_allow_incompatible(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()

    install_plugin_archive(buf, "plugin1", allow_incompatible=True)
    assert ("plugin1", "1.0.0") in get_installed_plugins()


def build_incompatible_repo(tmp_path: Path) -> Path:
    """A repository directory holding one plugin that supports neither the
    current IDA version nor the current platform."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    buf = rewrite_plugin_metadata(
        (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes(),
        idaVersions=["9.0"],
        platforms=[get_foreign_platform()],
    )
    (repo_dir / "plugin1-v1.0.0.zip").write_bytes(buf)
    return repo_dir


def test_repo_lookup_allowing_incompatible_falls_back(virtual_ida_environment, tmp_path):
    repo = FileSystemPluginRepo(build_incompatible_repo(tmp_path))
    current_platform = os.environ["HCLI_CURRENT_IDA_PLATFORM"]

    with pytest.raises(KeyError):
        repo.find_compatible_plugin_from_spec("plugin1", current_platform, "9.1")

    location = repo.find_plugin_from_spec_allowing_incompatible("plugin1", current_platform, "9.1")
    assert location.metadata.plugin.version == "1.0.0"


def test_repo_lookup_allowing_incompatible_prefers_compatible_archive(virtual_ida_environment, tmp_path):
    repo_dir = build_incompatible_repo(tmp_path)
    # a second, fully compatible version the relaxed lookup must prefer
    (repo_dir / "plugin1-v2.0.0.zip").write_bytes((PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes())
    repo = FileSystemPluginRepo(repo_dir)

    location = repo.find_plugin_from_spec_allowing_incompatible(
        "plugin1", os.environ["HCLI_CURRENT_IDA_PLATFORM"], "9.1"
    )
    assert location.metadata.plugin.version == "2.0.0"


def test_cli_install_incompatible_plugin(virtual_ida_environment, tmp_path):
    repo_dir = build_incompatible_repo(tmp_path)
    runner = CliRunner()

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "plugin1"], obj={})
    assert result.exit_code != 0
    assert not is_plugin_installed("plugin1")
    # the error must point at the flag that unblocks it
    assert "--allow-incompatible" in result.output

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "-I", "plugin1"], obj={})
    assert result.exit_code == 0, result.output
    assert is_plugin_installed("plugin1")


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


# A verbatim pip refusal under PEP 668, as run_pip formats it.
EXTERNALLY_MANAGED_PIP_ERROR = """\
/usr/bin/python3.12 -m pip install --dry-run --no-deps packaging==25.0
error: externally-managed-environment

x This environment is externally managed
+-> This Python installation is managed by the operating system.

note: If you believe this is a mistake, please contact your Python installation
or OS distribution provider. You can override this, at the risk of breaking your
Python installation or OS, by passing --break-system-packages.
hint: See PEP 668 for the detailed specification.
"""


def test_dependency_error_recognizes_externally_managed():
    e = DependencyInstallationError(["packaging==25.0"], EXTERNALLY_MANAGED_PIP_ERROR, Path("/usr/bin/python3.12"))
    assert e.is_externally_managed
    assert e.python_exe == Path("/usr/bin/python3.12")

    # any other pip failure must not be reported as a managed environment
    other = DependencyInstallationError(["packaging==25.0"], "No matching distribution found", Path("/x/python"))
    assert not other.is_externally_managed

    # and the reason is optional
    assert not DependencyInstallationError(["packaging==25.0"]).is_externally_managed


def test_cli_install_reports_externally_managed_environment(virtual_ida_environment, tmp_path, monkeypatch):
    """A PEP 668 refusal must explain how to get a virtualenv IDA will use,
    rather than leaving the user with pip's own --break-system-packages advice."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    # v3.0.0 declares pythonDependencies, so the pip path is exercised
    (repo_dir / "plugin1-v3.0.0.zip").write_bytes((PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip").read_bytes())

    python_exe = Path("/usr/bin/python3.12")
    monkeypatch.setattr("hcli.lib.ida.plugin.install.find_current_ida_python_executable", lambda: python_exe)
    monkeypatch.setattr("hcli.lib.ida.plugin.install.does_current_ida_have_pip", lambda _exe: True)

    def refuse(*args, **kwargs):
        raise CantInstallPackagesError(EXTERNALLY_MANAGED_PIP_ERROR)

    monkeypatch.setattr("hcli.lib.ida.plugin.install.verify_pip_can_install_packages", refuse)

    result = CliRunner().invoke(plugin_group, ["--repo", str(repo_dir), "install", "plugin1"], obj={})

    assert result.exit_code != 0
    assert not is_plugin_installed("plugin1")

    # names the interpreter, and the way out of it
    assert str(python_exe) in result.output
    assert "-m venv" in result.output
    assert "idapythonrc.py" in result.output
    # never repeat pip's own advice to break the system environment
    assert "--break-system-packages" not in result.output


def test_cli_install_does_not_explain_unrelated_dependency_failures(virtual_ida_environment, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plugin1-v3.0.0.zip").write_bytes((PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip").read_bytes())

    monkeypatch.setattr(
        "hcli.lib.ida.plugin.install.find_current_ida_python_executable", lambda: Path("/usr/bin/python3.12")
    )
    monkeypatch.setattr("hcli.lib.ida.plugin.install.does_current_ida_have_pip", lambda _exe: True)

    def refuse(*args, **kwargs):
        raise CantInstallPackagesError("ERROR: No matching distribution found for packaging==25.0")

    monkeypatch.setattr("hcli.lib.ida.plugin.install.verify_pip_can_install_packages", refuse)

    result = CliRunner().invoke(plugin_group, ["--repo", str(repo_dir), "install", "plugin1"], obj={})

    assert result.exit_code != 0
    assert "No matching distribution found" in result.output
    assert "-m venv" not in result.output
