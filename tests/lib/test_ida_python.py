import pytest
from pathlib import Path

from hcli.lib.ida.python import CantInstallPackagesError, does_current_ida_have_pip, find_current_python_executable, verify_pip_can_install_packages


@pytest.mark.asyncio
async def test_find_current_python_executable_returns_path():
    """Test that find_current_python_executable returns a valid path."""
    result = await find_current_python_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "python" in result.name.lower()


@pytest.mark.asyncio
async def test_does_current_ida_have_pip():
    python_exe = await find_current_python_executable()
    assert await does_current_ida_have_pip(python_exe)


@pytest.mark.asyncio
async def test_verify_pip_can_install_packages():
    python_exe = await find_current_python_executable()

    await verify_pip_can_install_packages(python_exe, ["flare-capa"])

    await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0"])
    await verify_pip_can_install_packages(python_exe, ["flare-capa==1.0.0"])
    await verify_pip_can_install_packages(python_exe, ["flare-capa==1.0"])
    await verify_pip_can_install_packages(python_exe, ["flare-capa==1"])
    await verify_pip_can_install_packages(python_exe, ["flare-capa==1"])
    await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.2.0"])

    # unfortunately this fuzzy matching doesn't work
    with pytest.raises(CantInstallPackagesError):
        await verify_pip_can_install_packages(python_exe, ["flare-capa~=1"])

    # duplicates
    await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa==v1.0.0"])
        
    # obvious conflict
    with pytest.raises(CantInstallPackagesError):
        await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa==v1.2.0"])

    # unfortunately this doesn't work
    with pytest.raises(CantInstallPackagesError):
        await verify_pip_can_install_packages(python_exe, ["flare-capa==1", "flare-capa==v1.2.0"])

    with pytest.raises(CantInstallPackagesError):
        await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.0.0", "flare-capa>v1.2.0"])

    with pytest.raises(CantInstallPackagesError):
        await verify_pip_can_install_packages(python_exe, ["flare-capa==v1.2.0", "flare-capa<=v1.0.0"])
