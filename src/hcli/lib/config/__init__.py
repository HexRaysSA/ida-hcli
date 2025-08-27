import json
from pathlib import Path
from typing import Any, TypeVar

from platformdirs import user_config_dir

from hcli.env import ENV

T = TypeVar("T")


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
            try:
                with open(self._config_file, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save_config(self):
        """Save configuration to disk."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, "w") as f:
            json.dump(self._data, f, indent=2)

    def _migrate_config(self):
        """Migrate configuration if version changed."""
        current_version = self.get_string("version", "0.0.0")
        if current_version != ENV.HCLI_VERSION:
            self.set_string("version", ENV.HCLI_VERSION)
            self._save_config()

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
