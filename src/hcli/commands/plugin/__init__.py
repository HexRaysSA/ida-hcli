from __future__ import annotations

import os
from pathlib import Path

import httpx
import rich_click as click

import hcli.lib.ida.plugin.repo
import hcli.lib.ida.plugin.repo.file
import hcli.lib.ida.plugin.repo.fs
import hcli.lib.ida.plugin.repo.github
from hcli.lib.console import console
from hcli.lib.ida import get_default_plugin_repository_name, get_ida_config, get_plugin_repositories
from hcli.lib.ida.plugin.reference import PluginReference
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo, is_plugin_bundle_zip
from hcli.lib.ida.python import PipOptions

from .bundle import bundle
from .config import config
from .install import install_plugin
from .lint import lint_plugin_directory
from .repo import repo
from .schema import schema
from .search import search_plugins
from .status import get_plugin_status
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


def read_repos_file(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"file doesn't exist: {path}")

    repos = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        if line.startswith("#"):
            continue

        repos.append(line.strip())

    return repos


@click.group()
@click.option(
    "--repo",
    help="'github', path to directory containing plugins, path to JSON file, URL to JSON file, or path to a plugin bundle .zip",
    hidden=True,
)
@click.option("--with-repos-list", help="path to file containing known GitHub repositories", hidden=True)
@click.option("--with-ignored-repos-list", help="path to file containing ignored GitHub repositories", hidden=True)
@click.option("--pip-index-url", help="pip --index-url for dependency installation", hidden=True)
@click.option("--pip-extra-index-url", multiple=True, help="pip --extra-index-url (repeatable)", hidden=True)
@click.option("--pip-find-links", multiple=True, help="pip --find-links (repeatable)", hidden=True)
@click.option(
    "--offline", is_flag=True, default=False, help="force pip to use only local sources (--no-index)", hidden=True
)
@click.pass_context
def plugin(
    ctx,
    repo: str | None,
    with_repos_list: str | None,
    with_ignored_repos_list: str | None,
    pip_index_url: str | None,
    pip_extra_index_url: tuple[str, ...],
    pip_find_links: tuple[str, ...],
    offline: bool,
) -> None:
    """Manage IDA Pro plugins."""
    # TODO: cleanup list and anything else touching github
    ctx.ensure_object(dict)

    pip_options = PipOptions(
        index_url=pip_index_url,
        extra_index_urls=pip_extra_index_url,
        find_links=tuple(Path(p).expanduser() if "://" not in p else p for p in pip_find_links),
        offline=offline,
    )
    ctx.obj["pip_options"] = pip_options

    # These subcommands don't touch the plugin repository, so skip the setup.
    if ctx.invoked_subcommand in ("schema", "explain-environment", "uninstall", "config", "lint"):
        return

    plugin_repo: hcli.lib.ida.plugin.repo.BasePluginRepo
    try:
        if repo is None:
            # One read of ida-config.json for both the map and the default.
            ida_config = get_ida_config()
            repositories = get_plugin_repositories(ida_config)
            if not repositories:
                console.print(
                    "[red]No plugin repositories configured[/red]. "
                    "Provide these in ida-config.json (.Settings.plugin-repositories)"
                )
                raise click.Abort()

            # Repositories are fetched lazily, so building the aggregate costs
            # nothing until a command actually looks something up.
            aggregate = AggregatePluginRepo(repositories)
            ctx.obj["plugin_repos"] = aggregate
            ctx.obj["default_plugin_repo"] = get_default_plugin_repository_name(ida_config)
            plugin_repo = aggregate

        elif repo == "github":
            try:
                token = os.environ["GITHUB_TOKEN"]
            except KeyError:
                console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable.")
                raise click.Abort()

            extra_repos = []
            if with_repos_list is not None:
                repos_list_path = Path(with_repos_list)
                try:
                    extra_repos = read_repos_file(repos_list_path)
                except ValueError as e:
                    console.print(f"[red]failed to read repos list file[/red]: {e!s}.")
                    raise click.Abort()

            ignored_repos = []
            if with_ignored_repos_list is not None:
                ignored_repos_list_path = Path(with_ignored_repos_list)
                try:
                    ignored_repos = read_repos_file(ignored_repos_list_path)
                except ValueError as e:
                    console.print(f"[red]failed to read ignored repos list file[/red]: {e!s}.")
                    raise click.Abort()

            plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(
                token, extra_repos=extra_repos, ignored_repos=ignored_repos
            )

        else:
            path = Path(repo)
            if not path.exists():
                console.print(
                    "[red]Repository doesn't exist[/red]. Provide `--repo github` or `--repo /path/to/plugins/`."
                )
                raise click.Abort()

            if path.is_dir():
                plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(path)
            elif is_plugin_bundle_zip(path):
                plugin_repo = PluginBundleRepo(path)
            else:
                plugin_repo = hcli.lib.ida.plugin.repo.file.JSONFilePluginRepo.from_file(path)

    except (httpx.ConnectError, httpx.TimeoutException):
        if repo == "github":
            console.print("[red]Cannot connect to GitHub - network unavailable.[/red]")
        elif repo is None:
            console.print("[red]Cannot connect to the plugin repositories - network unavailable.[/red]")
        else:
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
        console.print("Please check your internet connection.")
        raise click.Abort()

    ctx.obj["plugin_repo"] = plugin_repo
    ctx.obj.setdefault("plugin_repos", None)

    if offline and not pip_find_links and not isinstance(plugin_repo, PluginBundleRepo):
        console.print("[red]--offline requires --pip-find-links or a plugin bundle repository[/red]")
        raise click.Abort()


def repo_for_reference(ctx: click.Context, ref: PluginReference) -> hcli.lib.ida.plugin.repo.BasePluginRepo:
    """The repository a reference should be resolved against.

    Unprefixed references resolve in the default repository; a "repo/" prefix
    resolves in that repository alone. Under an explicit --repo there is only
    one repository to search, so a prefix has nothing to select and is refused
    rather than silently ignored.
    """
    aggregate: AggregatePluginRepo | None = ctx.obj.get("plugin_repos")

    if aggregate is None:
        if ref.repo:
            console.print(
                f"[red]Cannot use the repository prefix '{ref.repo}/' with --repo[/red]: "
                f"--repo already selects the only repository searched."
            )
            raise click.Abort()
        return ctx.obj["plugin_repo"]

    name = ref.repo or ctx.obj["default_plugin_repo"]
    if name not in aggregate.repositories:
        if ref.repo:
            known = ", ".join(sorted(aggregate.repositories)) or "none"
            console.print(f"[red]Unknown plugin repository '{ref.repo}'[/red]. Configured: {known}")
        else:
            console.print(
                f"[red]The default plugin repository '{name}' is not configured[/red]. "
                f"Fix .Settings.default-plugin-repository in ida-config.json."
            )
        raise click.Abort()

    # A named scope is a request for THAT repository: if it cannot be reached,
    # that is the answer, not a quietly smaller search.
    plugins = aggregate.get_plugins_in(name)

    # The survey path reports these through the search result; on this path
    # there is no result to carry them, so say it here rather than drop a
    # plugin silently.
    for note in aggregate.notes():
        console.print(f"[yellow]Warning:[/yellow] repository {note}")

    return hcli.lib.ida.plugin.repo.file.JSONFilePluginRepo(plugins)


plugin.add_command(get_plugin_status, name="status")


@click.command(name="list", hidden=True)
@click.argument("plugins", nargs=-1)
@click.option("--skip-upgrade-check", is_flag=True, default=False)
@click.option("--json", "json_output", is_flag=True, default=False)
@click.pass_context
def _plugin_status_alias(ctx, plugins: tuple[str, ...], skip_upgrade_check: bool, json_output: bool) -> None:
    """Show installed plugins and their upgrade status (alias for 'status')."""
    ctx.forward(get_plugin_status)


plugin.add_command(_plugin_status_alias)
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(lint_plugin_directory, name="lint")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
plugin.add_command(repo, name="repo")
plugin.add_command(config, name="config")
plugin.add_command(schema, name="schema")
plugin.add_command(bundle, name="bundle")


# Backwards-compat alias. Remove after 0.20.
@click.command(name="explain-environment", hidden=True)
@click.option("--json", "json_output", is_flag=True, default=False)
@click.pass_context
def _explain_environment_alias(ctx, json_output: bool) -> None:
    """Show how the current IDA installation and Python version are detected.

    Moved to `hcli ida python explain-environment`.
    """
    from hcli.commands.ida.python.explain_environment import explain_environment

    ctx.forward(explain_environment)


plugin.add_command(_explain_environment_alias)
