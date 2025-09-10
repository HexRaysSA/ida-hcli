import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from pydantic import RootModel

from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin

PluginList = RootModel[list[Plugin]]


class JSONFilePluginRepo(BasePluginRepo):
    def __init__(self, plugins: list[Plugin]):
        super().__init__()
        self.plugins = plugins

    def get_plugins(self) -> list[Plugin]:
        return self.plugins

    def to_json(self):
        doc = PluginList(self.get_plugins()).model_dump_json()
        # pydantic doesn't have a way to emit json with sorted keys
        # and we want a deterministic file,
        # so we re-encode here.
        return json.dumps(json.loads(doc), sort_keys=True, indent=4)

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_json(cls, doc: str):
        return cls(PluginList.model_validate_json(doc).root)

    @classmethod
    def from_bytes(cls, buf: bytes):
        return cls.from_json(buf.decode("utf-8"))

    @classmethod
    def from_file(cls, path: Path):
        return cls.from_bytes(path.read_bytes())

    @classmethod
    def from_url(cls, url: str):
        parsed_url = urlparse(url)

        if parsed_url.scheme == "file":
            # Handle file:// URLs
            file_path = Path(parsed_url.path)
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            return cls.from_bytes(file_path.read_bytes())

        elif parsed_url.scheme in ("http", "https"):
            # Handle HTTP(S) URLs
            response = requests.get(url, timeout=30.0)
            response.raise_for_status()
            return cls.from_bytes(response.content)

        else:
            raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")

    @classmethod
    def from_repo(cls, other: BasePluginRepo):
        return cls(other.get_plugins())
