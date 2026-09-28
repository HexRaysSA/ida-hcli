"""Layout shared by plugin listings that span repositories: `search` and `status`."""

from __future__ import annotations

from hcli.lib.ida import get_plugin_repository_sort_key


def get_repository_group_sort_key(repo: str | None) -> tuple[bool, tuple[bool, str]]:
    """Order plugin groups by repository; plugins with no known repository go last."""
    return (repo is None, get_plugin_repository_sort_key(repo or ""))


def render_plugin_label(name: str, repo: str | None, default_repo: str | None) -> str:
    """The string a user types to install this plugin, as Rich markup.

    A plugin from the default repository installs by bare name. Any other
    plugin needs its "repo/" prefix, which is shown in grey.
    """
    if repo and repo != default_repo:
        return f"[grey69]{repo}/[/grey69]{name}"
    return name
