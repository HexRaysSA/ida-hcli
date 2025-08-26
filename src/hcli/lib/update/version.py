"""Version checking utilities."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from packaging.version import Version, parse
from platformdirs import user_cache_dir
from semantic_version import SimpleSpec

from hcli import __version__
from hcli.env import ENV
from hcli.lib.update.release import GitHubRepo, get_compatible_version


async def get_latest_pypi_version(package_name: str) -> Version | None:
    """Get the latest version of a package from PyPI.

    Args:
        package_name: Name of the package on PyPI

    Returns:
        Latest stable version or None if not found
    """
    url = f"https://pypi.org/pypi/{package_name}/json"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()

            data = response.json()
            releases = data.get("releases", {})

            latest_stable = parse("0.0.0")

            for version_str in releases:
                try:
                    version = parse(version_str)
                    if not version.is_prerelease and version > latest_stable:
                        latest_stable = version
                except Exception:
                    # Skip invalid version strings (common in PyPI data)
                    pass

            return latest_stable if latest_stable > parse("0.0.0") else None

    except Exception:
        return None


def compare_versions(current: str, latest: Version) -> bool:
    """Compare current version with latest version.

    Args:
        current: Current version string
        latest: Latest version from PyPI

    Returns:
        True if an update is available
    """
    try:
        current_version = parse(current)
        return latest > current_version
    except Exception:
        return False


def is_binary():
    return getattr(sys, "frozen", False)


class BackgroundUpdateChecker:
    """Manages background checking for CLI updates."""

    def __init__(self, check_interval_hours: int = 24, cache_enabled: bool = True):
        """Initialize the background update checker.

        Args:
            cache_dir: Directory to store cache files (defaults to user cache dir)
            check_interval_hours: Hours between update checks
            cache_enabled: Whether to use caching for update checks
        """
        self.repo = GitHubRepo.from_url(ENV.HCLI_GITHUB_URL)
        self.cache_dir = Path(user_cache_dir(ENV.HCLI_BINARY_NAME, "hex-rays"))
        self.cache_file = self.cache_dir / "update_check.json"
        self.check_interval = timedelta(hours=check_interval_hours)
        self.cache_enabled = cache_enabled
        self.check_thread: threading.Thread | None = None
        self.result: str | None = None
        self.check_complete = threading.Event()

    def should_check(self) -> bool:
        """Determine if we should check for updates based on cache."""
        if not self.cache_enabled:
            return True

        if not self.cache_file.exists():
            return True

        try:
            with open(self.cache_file) as f:
                cache_data = json.load(f)

            last_check = datetime.fromisoformat(cache_data.get("last_check", ""))
            return datetime.now() - last_check > self.check_interval
        except Exception:
            return True

    def _save_cache(self, latest_version: Version | None, update_available: bool) -> None:
        """Save check results to cache."""
        if not self.cache_enabled:
            return

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "last_check": datetime.now().isoformat(),
                "latest_version": str(latest_version) if latest_version else None,
            }
            with open(self.cache_file, "w") as f:
                json.dump(cache_data, f)
        except Exception:
            pass  # Silently ignore cache write errors

    def _load_cached_result(self) -> str | None:
        """Load cached update result if available and recent."""
        if not self.cache_enabled:
            return None

        try:
            with open(self.cache_file) as f:
                cache_data = json.load(f)

            # Check if cache is recent enough
            last_check = datetime.fromisoformat(cache_data.get("last_check", ""))
            if datetime.now() - last_check > self.check_interval:
                return None

            update_available = compare_versions(__version__, latest=cache_data.get("latest_version", __version__))
            latest = cache_data.get("latest_version")
            if update_available:
                return self._format_update_message(__version__, latest)

        except Exception:
            pass
        return None

    def _check_for_updates(self) -> None:
        """Background thread function to check for updates."""
        try:
            include_dev = "dev" in ENV.HCLI_VERSION
            current_version = ENV.HCLI_VERSION
            latest_version = get_compatible_version(
                self.repo, SimpleSpec(f">{current_version}"), include_dev=include_dev
            )

            if latest_version is None:
                self._save_cache(None, False)
                self.result = self._format_no_update_message(current_version, str(latest_version))
                return

            self._save_cache(latest_version, True)

            self.result = self._format_update_message(current_version, str(latest_version))

        except Exception:
            # Silently ignore errors in background thread
            pass
        finally:
            self.check_complete.set()

    def _format_update_message(self, current: str, latest: str) -> str:
        """Format the update notification message."""
        return (
            f"\n[yellow]Update available![/yellow] "
            f"[dim]{current}[/dim] → [green]{latest}[/green]\n"
            f"[dim]Run[/dim] [bold cyan]uv tool upgrade ida-hcli[/bold cyan] [dim]to update[/dim]\n"
        )

    def _format_no_update_message(self, current: str, latest: str) -> str:
        """Format the update notification message."""
        return f"\n[yellow]You have the latest version {current}![/yellow] "

    def start_check(self) -> None:
        """Start background update check if needed."""
        # First check if we have a recent cached result
        cached_result = self._load_cached_result()

        if cached_result:
            self.result = cached_result
            self.check_complete.set()
            return

        # Only start background check if needed
        if not self.should_check():
            self.check_complete.set()
            return

        # Start background thread
        self.check_thread = threading.Thread(target=self._check_for_updates, daemon=True, name="hcli-update-check")

        self.check_thread.start()

    def get_result(self, timeout: float = 0.1) -> str | None:
        """Get update message if check completed.

        Args:
            timeout: Maximum time to wait for background check

        Returns:
            Update message if available, None otherwise
        """
        if self.check_complete.wait(timeout):
            return self.result
        return None
