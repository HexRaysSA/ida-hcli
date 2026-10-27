from pathlib import Path

from click.testing import CliRunner

from hcli.commands.ida.install import install, select_license_files
from hcli.lib.ida import IdaProduct

CURRENT = Path("idapro_96-0000-0000-01.hexlic")
LEGACY = Path("idapro_v90_96-0000-0000-01.hexlic")


def _version(major: int, minor: int) -> IdaProduct:
    return IdaProduct(product="IDA Professional", major=major, minor=minor)


def test_ida_before_9_3_gets_only_the_9_0_license_format():
    """IDA before 9.3 rejects a license with add-ons it does not know (#429)."""
    assert select_license_files([CURRENT, LEGACY], _version(9, 2)) == [LEGACY]


def test_ida_before_9_3_gets_the_current_format_when_there_is_no_9_0_format():
    assert select_license_files([CURRENT], _version(9, 0)) == [CURRENT]


def test_ida_9_3_and_later_gets_only_the_current_license_format():
    assert select_license_files([CURRENT, LEGACY], _version(9, 3)) == [CURRENT]
    assert select_license_files([CURRENT, LEGACY], _version(9, 5)) == [CURRENT]


def test_install_into_existing_non_ida_directory_fails(tmp_path):
    """Refusing to install into an existing directory without IDA must not report success (#398)."""
    installer = tmp_path / "ida-free-pc_94_armlinux.run"
    installer.write_bytes(b"")
    install_dir = tmp_path / "existing"
    install_dir.mkdir()
    (install_dir / "user-file").write_text("keep")

    result = CliRunner().invoke(install, [str(installer), "--install-dir", str(install_dir), "--yes"])

    assert result.exit_code == 1
    assert "Directory already exists" in result.output
    assert "Install failed" not in result.output
    assert [p.name for p in install_dir.iterdir()] == ["user-file"]
