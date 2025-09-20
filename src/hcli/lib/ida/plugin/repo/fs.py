import os
from pathlib import Path

from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex


class FileSystemPluginRepo(BasePluginRepo):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def get_plugins(self) -> list[Plugin]:
        index = PluginArchiveIndex()

        for root, dirs, files in os.walk(self.path):
            for file in files:
                if not file.endswith(".zip"):
                    continue

                path = Path(os.path.join(root, file))
                url = path.absolute().as_uri()

                with open(path, "rb") as f:
                    buf = f.read()

                index.index_plugin_archive(buf, url)

        return index.get_plugins()
