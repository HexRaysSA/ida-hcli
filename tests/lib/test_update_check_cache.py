from pathlib import Path

from packaging.version import Version

from hcli.lib.update.version import BackgroundUpdateChecker
from hcli.lib.util.cache import UPDATE_CACHE_KEY
from hcli.lib.util.cache_clear import apply_clear_plan, get_clear_plan


def test_update_check_result_is_in_the_update_category_of_the_cache_root(tmp_path: Path):
    root = tmp_path / "cache"
    BackgroundUpdateChecker(root)._save_cache(Version("99.0.0"), True)

    assert (root / UPDATE_CACHE_KEY / "update_check.json").is_file()
    assert not BackgroundUpdateChecker(root).should_check()

    plan = get_clear_plan(root)
    assert [c.category.key for c in plan.categories if c.has_data] == [UPDATE_CACHE_KEY]

    apply_clear_plan(plan)
    assert BackgroundUpdateChecker(root).should_check()
