from click.testing import CliRunner

from hcli.commands.ida.install import install


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
