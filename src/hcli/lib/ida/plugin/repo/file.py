import json
import urllib.request
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin


class StaticPluginRepo(BaseModel):
    version: Literal[1] = 1
    plugins: list[Plugin]


class JSONFilePluginRepo(BasePluginRepo):
    def __init__(self, plugins: list[Plugin]):
        super().__init__()
        self.plugins = plugins

    def get_plugins(self) -> list[Plugin]:
        return self.plugins

    def to_json(self):
        doc = StaticPluginRepo(plugins=self.get_plugins()).model_dump_json()
        # pydantic doesn't have a way to emit json with sorted keys
        # and we want a deterministic file,
        # so we re-encode here.
        return json.dumps(json.loads(doc), sort_keys=True, indent=4)

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_json(cls, doc: str):
        return cls(StaticPluginRepo.model_validate_json(doc).plugins)

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
            file_path = Path(urllib.request.url2pathname(parsed_url.path))
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            return cls.from_bytes(file_path.read_bytes())

        elif parsed_url.scheme == "https":
            response = requests.get(url, timeout=30.0)
            response.raise_for_status()
            return cls.from_bytes(response.content)

        else:
            raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")

    @classmethod
    def from_repo(cls, other: BasePluginRepo):
        return cls(other.get_plugins())
