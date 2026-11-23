"""Measure and delete the data in hcli's cache categories, for `hcli cache clear`."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from rich.filesize import decimal

from hcli.lib.util.cache import CACHE_CATEGORIES, CacheCategory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryPlan:
    category: CacheCategory
    path: Path
    files: tuple[Path, ...]
    size: int


@dataclass(frozen=True)
class ClearPlan:
    root: Path
    categories: tuple[CategoryPlan, ...]

    @property
    def total_bytes(self) -> int:
        return sum(c.size for c in self.categories)

    @property
    def is_empty(self) -> bool:
        return not any(c.files for c in self.categories)


@dataclass(frozen=True)
class ClearFailure:
    path: Path
    error: str
    size: int


@dataclass(frozen=True)
class ClearResult:
    plan: ClearPlan
    failures: list[ClearFailure] = field(default_factory=list)

    @property
    def failed_bytes(self) -> int:
        return sum(f.size for f in self.failures)

    @property
    def deleted_bytes(self) -> int:
        return self.plan.total_bytes - self.failed_bytes


class CategoryReport(BaseModel):
    name: str
    bytes: int


class FailureReport(BaseModel):
    path: str
    error: str


class ClearReport(BaseModel):
    dry_run: bool
    categories: list[CategoryReport]
    total_bytes: int
    failed_bytes: int
    failures: list[FailureReport]


def _get_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _get_category_files(path: Path) -> list[Path]:
    """List the files and symlinks under `path`, without following symlinks below it."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda e: logger.debug("cannot list: %s", e)):
        base = Path(dirpath)
        files.extend(base / name for name in filenames)
        files.extend(base / name for name in dirnames if (base / name).is_symlink())
    return sorted(files)


def get_clear_plan(root: Path) -> ClearPlan:
    """Measure the data in each cache category under `root`, without creating folders."""
    categories = []
    for category in CACHE_CATEGORIES:
        path = root / category.key
        files = _get_category_files(path) if path.is_dir() else []
        categories.append(CategoryPlan(category, path, tuple(files), sum(_get_size(f) for f in files)))
    return ClearPlan(root, tuple(categories))


def _remove_empty_subfolders(path: Path) -> None:
    for dirpath, _, _ in os.walk(path, topdown=False):
        if Path(dirpath) == path:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


def apply_clear_plan(plan: ClearPlan) -> ClearResult:
    """Delete the files in `plan`, then the empty subfolders of each category.

    A file that cannot be deleted is recorded as a failure, and deletion continues.
    The category folders themselves stay. Files added after planning are kept.
    """
    result = ClearResult(plan)
    for category in plan.categories:
        for path in category.files:
            size = _get_size(path)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug("cannot delete %s: %s", path, e)
                result.failures.append(ClearFailure(path, str(e), size))
        if category.files:
            _remove_empty_subfolders(category.path)
    return result


def get_clear_report(plan: ClearPlan, result: ClearResult | None) -> ClearReport:
    """Build the `--json` report. `result` is None for a dry run."""
    failures = result.failures if result is not None else []
    return ClearReport(
        dry_run=result is None,
        categories=[CategoryReport(name=c.category.key, bytes=c.size) for c in plan.categories],
        total_bytes=plan.total_bytes,
        failed_bytes=sum(f.size for f in failures),
        failures=[FailureReport(path=str(f.path), error=f.error) for f in failures],
    )


def render_size(size: int) -> str:
    return decimal(size)


def render_clear_listing(plan: ClearPlan) -> str:
    """Describe what `hcli cache clear` deletes, and say that deleting it is safe."""
    rows = [(c.category.description, render_size(c.size)) for c in plan.categories if c.files]
    name_width = max(len(name) for name, _ in rows)
    size_width = max(len(size) for _, size in rows)
    lines = ["hcli keeps these files so that it does not download them again:", ""]
    lines += [f"  {name:<{name_width}}   {size:>{size_width}}" for name, size in rows]
    lines += ["", "You can safely delete them at any time.", "hcli downloads them again when it needs them."]
    return "\n".join(lines)
