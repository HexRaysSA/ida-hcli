import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

DOWNLOADS_CACHE_KEY: Final = "downloads"
PACKS_CACHE_KEY: Final = "packs"
PLUGINS_CACHE_KEY: Final = "plugins"
UPDATE_CACHE_KEY: Final = "update"


@dataclass(frozen=True)
class CacheCategory:
    """A top-level folder of the cache root that `hcli cache clear` manages."""

    key: str
    description: str


CACHE_CATEGORIES: Final = (
    CacheCategory(DOWNLOADS_CACHE_KEY, "IDA installers and other downloaded files"),
    CacheCategory(PACKS_CACHE_KEY, "Lumina and FLIRT packs"),
    CacheCategory(PLUGINS_CACHE_KEY, "plugin repository data from GitHub"),
    CacheCategory(UPDATE_CACHE_KEY, "result of the last hcli update check"),
)


def get_cache_root(environ: Mapping[str, str], platform: str, home: Path) -> Path:
    r"""Compute the hcli cache root without touching the file system.

    `HCLI_CACHE_DIR` overrides the platform default when it is set and not empty:

    - Linux: `$XDG_CACHE_HOME/hex-rays/hcli`, or `~/.cache/hex-rays/hcli` when
      `XDG_CACHE_HOME` is unset, empty, or relative (XDG Base Directory spec)
    - macOS: `~/Library/Caches/hex-rays/hcli`
    - Windows: `%LOCALAPPDATA%\hex-rays\hcli\cache`

    Raises:
        NotImplementedError: the platform is not Linux, macOS, or Windows.
        KeyError: on Windows, `LOCALAPPDATA` is not set.
    """
    # set-but-empty must not become Path(""), the current directory
    cache_dir = environ.get("HCLI_CACHE_DIR")
    if cache_dir:
        return Path(cache_dir)

    # ref: https://github.com/mandiant/capa/issues/1212#issuecomment-1361259813
    # ref: https://stackoverflow.com/a/8220141/87207
    if platform == "linux" or platform == "linux2":
        # XDG_CACHE_HOME is a POSIX path, so check it with POSIX rules, also when the tests run on Windows
        # ref: https://specifications.freedesktop.org/basedir-spec/latest/
        xdg_cache_home = environ.get("XDG_CACHE_HOME", "")
        base = Path(xdg_cache_home) if PurePosixPath(xdg_cache_home).is_absolute() else home / ".cache"
        return base / "hex-rays" / "hcli"
    elif platform == "darwin":
        return home / "Library" / "Caches" / "hex-rays" / "hcli"
    elif platform == "win32":
        return Path(environ["LOCALAPPDATA"]) / "hex-rays" / "hcli" / "cache"
    else:
        raise NotImplementedError(f"unsupported platform: {platform}")


def get_default_cache_directory() -> Path:
    return get_cache_root(os.environ, sys.platform, Path.home())


def validate_path_component(name: str):
    if not name or name in {".", ".."}:
        raise ValueError(f"Invalid path component: '{name}'.")

    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"Invalid path component: '{name}'. Must contain only ASCII characters")

    if "\t" in name or "\n" in name or "\r" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain tabs or newlines")

    if "/" in name or "\\" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain slashes")


def get_cache_directory(*key: str) -> Path:
    dir = get_default_cache_directory()
    for k in key:
        validate_path_component(k)
        dir = dir / k
    dir.mkdir(parents=True, exist_ok=True)
    return dir
