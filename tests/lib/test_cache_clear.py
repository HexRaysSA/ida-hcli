import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hcli.lib.util.cache import DOWNLOADS_CACHE_KEY, PACKS_CACHE_KEY, PLUGINS_CACHE_KEY
from hcli.lib.util.cache_clear import (
    apply_clear_plan,
    get_clear_plan,
    get_clear_report,
    render_clear_listing,
)
from hcli.main import cli

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions and symlinks")


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A cache root with data in every category and two entries hcli does not manage."""
    root = tmp_path / "cache"
    write(root / DOWNLOADS_CACHE_KEY / "release" / "9.2" / "ida-pro" / "installer.run", 3000)
    write(root / DOWNLOADS_CACHE_KEY / "license.hexlic", 100)
    write(root / PACKS_CACHE_KEY / "abc.tar.zst", 800)
    write(root / PLUGINS_CACHE_KEY / "owner" / "repo" / "releases.json", 20)
    write(root / PLUGINS_CACHE_KEY / "candidate_repos.json", 10)
    write(root / "owner" / "repo" / "releases.json", 5)
    write(root / "notes.txt", 7)
    return root


def test_plan_of_missing_root_is_empty(tmp_path: Path):
    plan = get_clear_plan(tmp_path / "missing")

    assert plan.is_empty
    assert plan.total_bytes == 0
    assert [c.category.key for c in plan.categories] == [DOWNLOADS_CACHE_KEY, PACKS_CACHE_KEY, PLUGINS_CACHE_KEY]
    assert not (tmp_path / "missing").exists()


def test_plan_measures_each_category_and_skips_unmanaged_entries(root: Path):
    plan = get_clear_plan(root)

    assert {c.category.key: c.size for c in plan.categories} == {
        DOWNLOADS_CACHE_KEY: 3100,
        PACKS_CACHE_KEY: 800,
        PLUGINS_CACHE_KEY: 30,
    }
    assert plan.total_bytes == 3930
    planned = {f.path for c in plan.categories for f in c.files}
    assert root / "notes.txt" not in planned
    assert not any(f.is_relative_to(root / "owner") for f in planned)


def test_apply_deletes_files_and_empty_subfolders_and_keeps_the_rest(root: Path):
    result = apply_clear_plan(get_clear_plan(root))

    assert result.failures == []
    assert result.failed_bytes == 0
    for key in (DOWNLOADS_CACHE_KEY, PACKS_CACHE_KEY, PLUGINS_CACHE_KEY):
        assert (root / key).is_dir()
        assert list((root / key).iterdir()) == []
    assert (root / "notes.txt").is_file()
    assert (root / "owner" / "repo" / "releases.json").is_file()
    assert get_clear_plan(root).is_empty


def test_apply_ignores_files_removed_after_planning(root: Path):
    plan = get_clear_plan(root)
    (root / PACKS_CACHE_KEY / "abc.tar.zst").unlink()

    result = apply_clear_plan(plan)

    assert result.failures == []
    assert get_clear_plan(root).is_empty


def test_apply_keeps_files_added_after_planning(root: Path):
    plan = get_clear_plan(root)
    added = write(root / DOWNLOADS_CACHE_KEY / "release" / "9.3" / "new.run", 50)

    apply_clear_plan(plan)

    assert added.is_file()


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_apply_records_a_failure_and_continues(root: Path):
    plan = get_clear_plan(root)
    locked_dir = root / DOWNLOADS_CACHE_KEY / "release" / "9.2" / "ida-pro"
    locked_dir.chmod(0o500)
    try:
        result = apply_clear_plan(plan)
    finally:
        locked_dir.chmod(0o700)

    assert [f.path for f in result.failures] == [locked_dir / "installer.run"]
    assert result.failed_bytes == 3000
    assert result.deleted_bytes == 930
    assert (locked_dir / "installer.run").is_file()
    assert not (root / DOWNLOADS_CACHE_KEY / "license.hexlic").exists()
    assert not (root / PACKS_CACHE_KEY / "abc.tar.zst").exists()


def test_apply_removes_empty_subfolders_of_a_category_without_files(tmp_path: Path):
    root = tmp_path / "cache"
    (root / DOWNLOADS_CACHE_KEY / "release" / "9.2").mkdir(parents=True)

    apply_clear_plan(get_clear_plan(root))

    assert list((root / DOWNLOADS_CACHE_KEY).iterdir()) == []


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_unreadable_folder_is_reported_as_a_failure(tmp_path: Path):
    root = tmp_path / "cache"
    locked = root / PACKS_CACHE_KEY / "locked"
    write(locked / "abc.tar.zst", 10)
    locked.chmod(0o000)
    try:
        plan = get_clear_plan(root)
        result = apply_clear_plan(plan)
    finally:
        locked.chmod(0o700)

    assert not plan.is_empty
    assert [f.path for f in result.failures] == [locked]
    assert (locked / "abc.tar.zst").is_file()


@posix_only
def test_apply_removes_symlinks_without_following_them(tmp_path: Path):
    root = tmp_path / "cache"
    outside_file = write(tmp_path / "outside" / "keep.bin", 10)
    outside_dir = outside_file.parent
    (root / DOWNLOADS_CACHE_KEY).mkdir(parents=True)
    (root / DOWNLOADS_CACHE_KEY / "file-link").symlink_to(outside_file)
    (root / DOWNLOADS_CACHE_KEY / "dir-link").symlink_to(outside_dir, target_is_directory=True)

    plan = get_clear_plan(root)
    result = apply_clear_plan(plan)

    assert result.failures == []
    assert list((root / DOWNLOADS_CACHE_KEY).iterdir()) == []
    assert outside_file.read_bytes() == b"x" * 10


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_apply_removes_junctions_without_following_them(tmp_path: Path):
    root = tmp_path / "cache"
    outside_file = write(tmp_path / "outside" / "keep.bin", 10)
    junction = root / DOWNLOADS_CACHE_KEY / "junction"
    junction.parent.mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside_file.parent)], check=True)

    result = apply_clear_plan(get_clear_plan(root))

    assert result.failures == []
    assert list((root / DOWNLOADS_CACHE_KEY).iterdir()) == []
    assert outside_file.read_bytes() == b"x" * 10


def test_listing_names_categories_with_data_and_says_deleting_is_safe(tmp_path: Path):
    root = tmp_path / "cache"
    write(root / DOWNLOADS_CACHE_KEY / "installer.run", 3_100_000)
    write(root / PACKS_CACHE_KEY / "abc.tar.zst", 812_000)

    listing = render_clear_listing(get_clear_plan(root))

    assert "IDA installers and other downloaded files" in listing
    assert "3.1 MB" in listing
    assert "Lumina and FLIRT packs" in listing
    assert "812.0 kB" in listing
    assert "plugin repository data" not in listing
    assert "safely delete" in listing
    assert str(root) not in listing


def test_report_of_a_dry_run(root: Path):
    plan = get_clear_plan(root)
    report = get_clear_report(plan, None, dry_run=True)

    assert report.model_dump(mode="json") == {
        "dry_run": True,
        "categories": [
            {"name": DOWNLOADS_CACHE_KEY, "bytes": 3100},
            {"name": PACKS_CACHE_KEY, "bytes": 800},
            {"name": PLUGINS_CACHE_KEY, "bytes": 30},
        ],
        "total_bytes": 3930,
        "deleted_bytes": 0,
        "failed_bytes": 0,
        "failures": [],
    }


def invoke(root: Path, *args: str):
    return CliRunner(mix_stderr=False).invoke(cli, ["cache", "clear", *args], env={"HCLI_CACHE_DIR": str(root)})


def test_cli_reports_an_empty_cache(tmp_path: Path):
    result = invoke(tmp_path / "missing")

    assert result.exit_code == 0
    assert "The cache is empty." in result.stdout
    assert not (tmp_path / "missing").exists()


def test_cli_dry_run_lists_and_deletes_nothing(root: Path):
    result = invoke(root, "--dry-run")

    assert result.exit_code == 0
    assert "IDA installers and other downloaded files" in result.stdout
    assert "safely delete" in result.stdout
    assert get_clear_plan(root).total_bytes == 3930


def test_cli_without_a_terminal_requires_yes(root: Path):
    result = invoke(root)

    assert result.exit_code == 1
    assert "--yes" in result.stdout
    assert get_clear_plan(root).total_bytes == 3930


def test_cli_yes_deletes_the_cache(root: Path):
    result = invoke(root, "--yes")

    assert result.exit_code == 0
    assert "Deleted 3.9 kB of cached files." in result.stdout
    assert get_clear_plan(root).is_empty
    assert (root / "notes.txt").is_file()


def test_cli_json(root: Path):
    result = invoke(root, "--yes", "--json")

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["dry_run"] is False
    assert report["total_bytes"] == 3930
    assert report["failed_bytes"] == 0
    assert get_clear_plan(root).is_empty


def test_cli_dry_run_json(root: Path):
    result = invoke(root, "--dry-run", "--json")

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["dry_run"] is True
    assert report["total_bytes"] == 3930
    assert get_clear_plan(root).total_bytes == 3930


def test_cli_json_of_an_empty_cache_is_not_a_dry_run(tmp_path: Path):
    result = invoke(tmp_path / "missing", "--yes", "--json")

    assert result.exit_code == 0
    assert json.loads(result.stdout)["dry_run"] is False


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_cli_exits_1_when_a_file_cannot_be_deleted(root: Path):
    locked_dir = root / DOWNLOADS_CACHE_KEY / "release" / "9.2" / "ida-pro"
    locked_dir.chmod(0o500)
    try:
        result = invoke(root, "--yes")
    finally:
        locked_dir.chmod(0o700)

    assert result.exit_code == 1
    assert "could not be deleted" in result.stdout
    assert (locked_dir / "installer.run").is_file()


def test_cli_json_without_yes_fails(root: Path):
    result = invoke(root, "--json")

    assert result.exit_code == 1
    assert "--yes" in json.loads(result.stdout)["error"]
    assert get_clear_plan(root).total_bytes == 3930
