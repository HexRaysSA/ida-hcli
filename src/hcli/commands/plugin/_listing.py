"""Layout shared by plugin listings that span repositories: `search` and `status`."""

from __future__ import annotations

from hcli.lib.ida import get_plugin_repository_sort_key


def get_repository_group_sort_key(repo: str | None) -> tuple[bool, tuple[bool, str]]:
    """Order plugin groups by repository; plugins with no known repository go last."""
    return (repo is None, get_plugin_repository_sort_key(repo or ""))


def get_install_prefix(repo: str | None, default_repo: str | None) -> str:
    """The "repo/" prefix a user types before a plugin name to install it.

    A plugin from the default repository installs by bare name, so it has none.
    """
    if repo and repo != default_repo:
        return f"{repo}/"
    return ""


def format_install_reference(name: str, repo: str | None, default_repo: str | None) -> str:
    """The string a user types to install this plugin, as plain text."""
    return get_install_prefix(repo, default_repo) + name


def render_plugin_label(name: str, repo: str | None, default_repo: str | None) -> str:
    """The string a user types to install this plugin, as Rich markup with the prefix in grey."""
    prefix = get_install_prefix(repo, default_repo)
    return f"[grey69]{prefix}[/grey69]{name}" if prefix else name
