"""Structured result types for install and upgrade operations."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class InstallStatus(enum.Enum):
    SUCCESS = "success"
    ALREADY_INSTALLED = "already_installed"
    FAILED = "failed"


@dataclass(frozen=True)
class InstallResult:
    plugin: str
    version: str
    status: InstallStatus
    reason: str | None = None
    dependencies: list[InstallResult] = field(default_factory=list)
