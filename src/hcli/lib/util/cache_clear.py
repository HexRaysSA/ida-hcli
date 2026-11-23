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
class ClearFailure:
    path: Path
    error: str
    size: int


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    size: int


@dataclass(frozen=True)
class CategoryPlan:
    category: CacheCategory
    path: Path
    files: tuple[PlannedFile, ...]
    unreadable: tuple[ClearFailure, ...]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def has_data(self) -> bool:
        return bool(self.files or self.unreadable)


@dataclass(frozen=True)
class ClearPlan:
    root: Path
    categories: tuple[CategoryPlan, ...]

    @property
    def total_bytes(self) -> int:
        return sum(c.size for c in self.categories)

    @property
    def is_empty(self) -> bool:
        return not any(c.has_data for c in self.categories)


@dataclass
class ClearResult:
    deleted_bytes: int = 0
    failures: list[ClearFailure] = field(default_factory=list)

    @property
    def failed_bytes(self) -> int:
        return sum(f.size for f in self.failures)


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
    deleted_bytes: int
    failed_bytes: int
    failures: list[FailureReport]


def _get_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _get_category_plan(category: CacheCategory, path: Path) -> CategoryPlan:
    """List the files and symlinks under `path`, without following symlinks below it."""
    files: list[PlannedFile] = []
    unreadable: list[ClearFailure] = []

    def on_error(e: OSError) -> None:
        unreadable.append(ClearFailure(Path(e.filename or path), str(e), 0))

    if path.is_dir():
        for dirpath, dirnames, filenames in os.walk(path, onerror=on_error):
            base = Path(dirpath)
            links = [base / name for name in dirnames if (base / name).is_symlink()]
            for file in [base / name for name in filenames] + links:
                files.append(PlannedFile(file, _get_size(file)))
    return CategoryPlan(category, path, tuple(sorted(files, key=lambda f: f.path)), tuple(unreadable))


def get_clear_plan(root: Path) -> ClearPlan:
    """Measure the data in each cache category under `root`, without creating folders."""
    return ClearPlan(root, tuple(_get_category_plan(c, root / c.key) for c in CACHE_CATEGORIES))


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

    A file that cannot be deleted, or a folder that could not be listed, is recorded
    as a failure, and deletion continues. The category folders themselves stay.
    Files added after planning are kept.
    """
    result = ClearResult()
    for category in plan.categories:
        result.failures.extend(category.unreadable)
        for file in category.files:
            try:
                file.path.unlink()
            except FileNotFoundError:
                continue
            except OSError as e:
                result.failures.append(ClearFailure(file.path, str(e), file.size))
                continue
            result.deleted_bytes += file.size
        if category.path.is_dir():
            _remove_empty_subfolders(category.path)
    for failure in result.failures:
        logger.debug("cannot delete %s: %s", failure.path, failure.error)
    return result


def get_clear_report(plan: ClearPlan, result: ClearResult | None, *, dry_run: bool) -> ClearReport:
    """Build the `--json` report. `result` is None when nothing was deleted."""
    result = result or ClearResult()
    return ClearReport(
        dry_run=dry_run,
        categories=[CategoryReport(name=c.category.key, bytes=c.size) for c in plan.categories],
        total_bytes=plan.total_bytes,
        deleted_bytes=result.deleted_bytes,
        failed_bytes=result.failed_bytes,
        failures=[FailureReport(path=str(f.path), error=f.error) for f in result.failures],
    )


def render_size(size: int) -> str:
    return decimal(size)


def render_clear_listing(plan: ClearPlan) -> str:
    """Describe what `hcli cache clear` deletes, and say that deleting it is safe."""
    rows = [(c.category.description, render_size(c.size)) for c in plan.categories if c.has_data]
    name_width = max(len(name) for name, _ in rows)
    size_width = max(len(size) for _, size in rows)
    lines = ["hcli keeps these files so that it does not download them again:", ""]
    lines += [f"  {name:<{name_width}}   {size:>{size_width}}" for name, size in rows]
    lines += ["", "You can safely delete them at any time.", "hcli downloads them again when it needs them."]
    return "\n".join(lines)
