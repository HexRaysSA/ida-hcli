import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from platformdirs import user_config_dir

from hcli.env import ENV

T = TypeVar("T")

# config.json holds the account token, so only the owner may read it.
_CONFIG_DIR_MODE = 0o700
_CONFIG_FILE_MODE = 0o600


def _tighten_mode(path: Path, mode: int) -> None:
    """Remove group and other permissions from `path` when it has any (POSIX only)."""
    if os.name != "posix":
        return
    try:
        current = stat.S_IMODE(os.stat(path).st_mode)
        if current & 0o077:
            os.chmod(path, mode)
    except OSError:
        # missing, or owned by another user: leave it as it is
        pass


class ConfigStore:
    """Cross-platform configuration storage for hcli."""

    def __init__(self):
        self._config_dir = Path(user_config_dir("hcli", "hex-rays"))
        self._config_file = self._config_dir / "config.json"
        self._data: dict[str, Any] = {}
        self._load_config()
        self._migrate_config()

    def _load_config(self) -> None:
        """Load configuration from disk."""
        if self._config_file.exists():
            # fix configs written by older versions with umask-default permissions
            _tighten_mode(self._config_dir, _CONFIG_DIR_MODE)
            _tighten_mode(self._config_file, _CONFIG_FILE_MODE)
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save_config(self):
        """Save configuration to disk, readable only by the current user.

        The data is written to a temporary file created with mode 0600 and then
        moved over config.json, so the file is never readable by other users and a
        crash during the write does not leave a truncated config.
        """
        self._config_dir.mkdir(mode=_CONFIG_DIR_MODE, parents=True, exist_ok=True)
        _tighten_mode(self._config_dir, _CONFIG_DIR_MODE)

        # write through a symlinked config.json instead of replacing the link
        target = Path(os.path.realpath(self._config_file))
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _migrate_config(self):
        """Migrate configuration if version changed."""
        binary = ENV.HCLI_BINARY_NAME
        version_key = f"{binary}.version"
        current_version = self.get_string(version_key, "0.0.0")
        if current_version != ENV.HCLI_VERSION:
            self._migrate_legacy_keys(ENV.HCLI_CONFIG_NAMESPACE)
            self._data[version_key] = ENV.HCLI_VERSION
            self._save_config()

    def _migrate_legacy_keys(self, namespace: str):
        """Migrate unscoped config keys to namespaced auth keys."""
        for old_key, suffix in [("credentials", "credentials"), ("login.email", "login.email")]:
            new_key = f"{namespace}.{suffix}"
            if old_key in self._data and new_key not in self._data:
                self._data[new_key] = self._data[old_key]
                del self._data[old_key]

    def has(self, key: str) -> bool:
        """Check if key exists in configuration."""
        return key in self._data and self._data[key] is not None

    def get_string(self, key: str, default_value: str = "") -> str:
        """Get string value from configuration."""
        return self._data.get(key, default_value) or default_value

    def set_string(self, key: str, value: str = ""):
        """Set string value in configuration."""
        self._data[key] = value
        self._save_config()

    def remove_string(self, key: str):
        """Remove key from configuration."""
        if key in self._data:
            del self._data[key]
            self._save_config()

    def set_object(self, key: str, value: Any):
        """Set object value in configuration."""
        self._data[key] = value
        self._save_config()

    def get_object(self, key: str, default: T | None = None) -> T | None:
        """Get object value from configuration."""
        return self._data.get(key, default)


# Global config store instance
config_store = ConfigStore()
