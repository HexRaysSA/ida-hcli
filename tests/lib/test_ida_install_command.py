import importlib
import os

from click.testing import CliRunner
from fixtures import get_base_python_exe, set_env_var, unset_env_var

from hcli.commands.ida.install import install, warn_python_environment_for_install
from hcli.lib.ida.python import PythonNotFoundError, ResolvedPython

# `hcli.commands.ida` re-exports the `install` command under the module's name.
install_module = importlib.import_module("hcli.commands.ida.install")


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


def test_install_warns_when_ida_uses_a_global_python(tmp_path, monkeypatch, capsys):
    set_env_var(monkeypatch, "HCLI_IDAUSR", str(tmp_path / "idausr"))
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR", "/previous/ida")
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")
    unset_env_var(monkeypatch, "VIRTUAL_ENV")
    install_dir = tmp_path / "ida"
    probed: list[str] = []

    def resolve() -> ResolvedPython:
        probed.append(os.environ["HCLI_CURRENT_IDA_INSTALL_DIR"])
        return ResolvedPython(get_base_python_exe(), "derived from idat probe")

    monkeypatch.setattr(install_module, "resolve_current_python", resolve)
    monkeypatch.setattr("hcli.lib.ida.python.environment.is_site_packages_writable", lambda exe: True)

    warn_python_environment_for_install(install_dir)

    assert probed == [str(install_dir)]
    assert os.environ["HCLI_CURRENT_IDA_INSTALL_DIR"] == "/previous/ida"
    err = capsys.readouterr().err
    assert "not a virtual environment" in err
    assert "ida python doctor" in err
    assert "--create-python-environment" in err
    assert "--no-python-environment-check" not in err


def test_install_skips_the_python_check_when_ida_cannot_be_probed(tmp_path, monkeypatch, capsys):
    unset_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR")

    def resolve() -> ResolvedPython:
        raise PythonNotFoundError("no idat")

    monkeypatch.setattr(install_module, "resolve_current_python", resolve)

    warn_python_environment_for_install(tmp_path / "ida")

    assert capsys.readouterr().err == ""
    assert "HCLI_CURRENT_IDA_INSTALL_DIR" not in os.environ
