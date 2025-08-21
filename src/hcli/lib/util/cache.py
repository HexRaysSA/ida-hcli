import os
import sys
from pathlib import Path


def get_default_cache_directory() -> Path:
    if "HCLI_CACHE_DIR" in os.environ:
        return Path(os.environ["HCLI_CACHE_DIR"])

    # otherwise use XDG_CACHE_HOME
    # ref: https://github.com/mandiant/capa/issues/1212#issuecomment-1361259813
    #
    # Linux:   $XDG_CACHE_HOME/hex-rays/hcli/
    # Windows: %LOCALAPPDATA%\hex-rays\hcli\cache
    # MacOS:   ~/Library/Caches/hex-rays/hcli/

    # ref: https://stackoverflow.com/a/8220141/87207
    if sys.platform == "linux" or sys.platform == "linux2":
        directory = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache" / "hex-rays" / "hcli"))
    elif sys.platform == "darwin":
        directory = Path.home() / "Library" / "Caches" / "hex-rays" / "hcli"
    elif sys.platform == "win32":
        directory = Path(os.environ["LOCALAPPDATA"]) / "hex-rays" / "hcli" / "cache"
    else:
        raise NotImplementedError(f"unsupported platform: {sys.platform}")

    directory.mkdir(parents=True, exist_ok=True)

    return directory


def validate_path_component(name: str):
    if not name or name == "." or name == "..":
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
