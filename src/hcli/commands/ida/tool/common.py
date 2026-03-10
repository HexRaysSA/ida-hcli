from __future__ import annotations

import re

from hcli.lib.api.asset import asset as asset_api

KNOWN_TOOLS = {"vault2git", "git-ida"}

# Matches release/<version>/sdk-and-utilities/<tool_name>
_TOOL_PATH_RE = re.compile(r"^release/([^/]+)/sdk-and-utilities/([^/]+)$")


async def fetch_tool_assets() -> list[tuple[str, str, str]]:
    """Fetch all tool assets from the installers bucket.

    Returns a list of (asset_key, tool_name, version) tuples,
    filtered to known tools under release/*/sdk-and-utilities/*.
    """
    paged = await asset_api.get_files("installers")
    results: list[tuple[str, str, str]] = []
    for item in paged.items:
        m = _TOOL_PATH_RE.match(item.key)
        if not m:
            continue
        version, tool_name = m.group(1), m.group(2)
        if tool_name.lower() in {t.lower() for t in KNOWN_TOOLS}:
            results.append((item.key, tool_name, version))
    return results
