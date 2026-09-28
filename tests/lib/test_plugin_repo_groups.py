"""Plugin listings that span repositories group their rows by repository."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *

from hcli.commands.plugin import plugin as plugin_group
from hcli.commands.plugin.search import collect_keyword_matches
from hcli.commands.plugin.status import InstalledPluginStatusEntry, collect_status_report
from hcli.lib.ida import (
    COMMUNITY_REPO_NAME,
    HEXRAYS_REPO_NAME,
    RESERVED_PLUGIN_REPOSITORIES,
    IDAConfigJson,
    PluginRepository,
    PluginRepositoryConfig,
    find_current_ida_platform,
    find_current_ida_version,
    get_plugin_repositories,
)
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo

# Each repository serves one plugin, named so that sorting by plugin name
# alone would give the opposite order to sorting by repository.
PLUGIN_BY_REPO = {
    COMMUNITY_REPO_NAME: "apple",
    "zeta": "banana",
    "alpha": "cherry",
}
EXPECTED_ORDER = [("alpha", "cherry"), ("zeta", "banana"), (COMMUNITY_REPO_NAME, "apple")]


def _write_plugin_zip(repo_dir: Path, name: str) -> None:
    metadata = {
        "IDAMetadataDescriptorVersion": 1,
        "plugin": {
            "name": name,
            "version": "1.0.0",
            "entryPoint": f"{name}.py",
            "urls": {"repository": f"https://github.com/test/{name}"},
            "authors": [{"name": "Test", "email": "test@example.com"}],
        },
    }
    repo_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(repo_dir / f"{name}-v1.0.0.zip", "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(metadata))
        zf.writestr(f"{name}/{name}.py", "# plugin")


@pytest.fixture
def repo_dirs(tmp_path) -> dict[str, Path]:
    dirs = {}
    for repo, name in PLUGIN_BY_REPO.items():
        dirs[repo] = tmp_path / repo
        _write_plugin_zip(dirs[repo], name)
    return dirs


@pytest.fixture
def aggregate(repo_dirs) -> AggregatePluginRepo:
    return AggregatePluginRepo(
        {repo: PluginRepository(name=repo, url=path.as_uri(), reserved=False) for repo, path in repo_dirs.items()}
    )


def test_repositories_sort_alphabetically_with_community_last():
    config = IDAConfigJson()
    config.settings.plugin_repositories = {
        "zeta": PluginRepositoryConfig(url="file:///tmp/zeta"),
        COMMUNITY_REPO_NAME: PluginRepositoryConfig(url=RESERVED_PLUGIN_REPOSITORIES[COMMUNITY_REPO_NAME]),
        "alpha": PluginRepositoryConfig(url="file:///tmp/alpha"),
        HEXRAYS_REPO_NAME: PluginRepositoryConfig(url=RESERVED_PLUGIN_REPOSITORIES[HEXRAYS_REPO_NAME]),
    }

    assert list(get_plugin_repositories(config)) == ["alpha", HEXRAYS_REPO_NAME, "zeta", COMMUNITY_REPO_NAME]


def test_search_orders_results_by_repository_group(virtual_ida_environment, aggregate):
    matches = collect_keyword_matches(
        aggregate.get_plugins(),
        "",
        find_current_ida_version(),
        find_current_ida_platform(),
        [],
        repo_of=aggregate.repo_of,
    )

    assert [(m.repo, m.name) for m in matches] == EXPECTED_ORDER


def test_status_orders_plugins_by_repository_group(virtual_ida_environment, tmp_path, repo_dirs, aggregate):
    orphan_dir = tmp_path / "orphan"
    _write_plugin_zip(orphan_dir, "aardvark")

    sources = [(repo_dirs[repo], name) for repo, name in PLUGIN_BY_REPO.items()] + [(orphan_dir, "aardvark")]
    runner = CliRunner(mix_stderr=False)
    for path, name in sources:
        result = runner.invoke(plugin_group, ["--repo", str(path), "install", name])
        assert result.exit_code == 0, result.output

    report = collect_status_report(aggregate, (), skip_upgrade_check=False, repo_of=aggregate.repo_of)

    entries = [e for e in report.plugins if isinstance(e, InstalledPluginStatusEntry)]
    assert [(e.repo, e.name) for e in entries] == [*EXPECTED_ORDER, (None, "aardvark")]


def _section_of(lines: list[str], text: str) -> int:
    """Index of the blank-line-separated section containing `text`."""
    section = 0
    for line in lines:
        if not line.strip():
            section += 1
        elif text in line:
            return section
    raise AssertionError(f"{text!r} not found in output")


def test_cli_renders_repository_sections(virtual_ida_environment, block_network, repo_dirs):
    runner = CliRunner(mix_stderr=False)

    def invoke(*args: str) -> str:
        result = runner.invoke(plugin_group, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    invoke("repo", "remove", HEXRAYS_REPO_NAME)
    invoke("repo", "remove", COMMUNITY_REPO_NAME)
    for repo in ("zeta", "alpha"):
        invoke("repo", "add", repo, repo_dirs[repo].as_uri())
    invoke("repo", "set-default", "zeta")
    invoke("install", "alpha/cherry")
    invoke("install", "banana")

    repo_list = invoke("repo", "list")
    assert repo_list.index("alpha") < repo_list.index("zeta")

    for output in (invoke("search"), invoke("status")):
        lines = output.splitlines()
        # The default repository's plugins install by bare name, others need the prefix.
        assert "alpha/cherry" in output
        assert "zeta/banana" not in output
        assert _section_of(lines, "alpha/cherry") < _section_of(lines, "banana")
