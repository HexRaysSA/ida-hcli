from __future__ import annotations

import subprocess
from datetime import datetime

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.ida import IdaVersion, get_default_ida_install_directory, get_ida_binary_path


@click.command(name="open", hidden=True)
@click.argument("url", required=True)
@async_command
async def open_url(url: str | None) -> None:
    """HCLI protocol handler for ida://"""

    ida_dir = get_default_ida_install_directory(IdaVersion("IDA Professional", 9, 2))
    ida_bin = get_ida_binary_path(ida_dir)

    # Log the URL to a temp file
    log_file = "/tmp/hcli_urls.log"
    timestamp = datetime.now().isoformat()

    with open(str(log_file), "a", encoding="utf-8") as f:
        f.write(f"{timestamp}: {url} : {ida_bin}\n")

    subprocess.Popen(["open", "-a", ida_bin])
