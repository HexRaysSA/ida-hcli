import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from platformdirs import user_config_dir

from hcli.env import ENV

T = TypeVar("T")


def _make_private(path: Path) -> None:
    """Remove the group and other permissions of `path`, on POSIX."""
    if os.name != "posix":
        return
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            path.chmod(mode & ~0o077)
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
            # fix a config that an older version wrote with umask-default permissions
            _make_private(self._config_dir)
            _make_private(self._config_file)
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save_config(self):
        """Save configuration to disk, readable only by the current user.

        Write to a temporary file and replace config.json with it, so a crash during the
        write does not leave a truncated config.
        """
        # config.json holds the account token, so other users must not read it
        self._config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir does not change the mode of a directory that exists already
        _make_private(self._config_dir)

        # write through a symlinked config.json instead of replacing the link
        target = self._config_file.resolve()
        # mkstemp creates the file with mode 0600, so other users can never read it
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
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
