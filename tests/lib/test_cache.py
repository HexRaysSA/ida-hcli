from pathlib import Path

import pytest

from hcli.lib.util.cache import get_cache_root

HOME = Path("/home/user")


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, HOME / ".cache" / "hex-rays" / "hcli"),
        ({"XDG_CACHE_HOME": "/var/cache/user"}, Path("/var/cache/user/hex-rays/hcli")),
        ({"XDG_CACHE_HOME": str(HOME / ".cache")}, HOME / ".cache" / "hex-rays" / "hcli"),
        ({"XDG_CACHE_HOME": ""}, HOME / ".cache" / "hex-rays" / "hcli"),
        ({"XDG_CACHE_HOME": "relative/cache"}, HOME / ".cache" / "hex-rays" / "hcli"),
    ],
)
def test_get_cache_root_linux(environ: dict[str, str], expected: Path):
    assert get_cache_root(environ, "linux", HOME) == expected


def test_get_cache_root_macos_ignores_xdg_cache_home():
    environ = {"XDG_CACHE_HOME": "/var/cache/user"}
    assert get_cache_root(environ, "darwin", HOME) == HOME / "Library" / "Caches" / "hex-rays" / "hcli"


def test_get_cache_root_windows():
    environ = {"LOCALAPPDATA": "/appdata/local"}
    assert get_cache_root(environ, "win32", HOME) == Path("/appdata/local/hex-rays/hcli/cache")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_get_cache_root_hcli_cache_dir_overrides_platform_default(platform: str):
    environ = {"HCLI_CACHE_DIR": "/srv/hcli", "XDG_CACHE_HOME": "/var/cache/user", "LOCALAPPDATA": "/appdata"}
    assert get_cache_root(environ, platform, HOME) == Path("/srv/hcli")


def test_get_cache_root_empty_hcli_cache_dir_uses_platform_default():
    assert get_cache_root({"HCLI_CACHE_DIR": ""}, "linux", HOME) == HOME / ".cache" / "hex-rays" / "hcli"


def test_get_cache_root_unsupported_platform():
    with pytest.raises(NotImplementedError):
        get_cache_root({}, "sunos5", HOME)
