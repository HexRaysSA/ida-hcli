"""Binary updater for frozen applications."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

from packaging.version import Version

from hcli.env import ENV
from hcli.lib.api.github import GitHubAPI, GitHubRelease
from hcli.lib.console import console
from hcli.lib.update.version import compare_versions, is_binary


class UpdateError(Exception):
    """Raised when update process fails."""

    pass


class BinaryUpdater:
    """Handles binary updates for frozen applications."""

    def __init__(self, github_url: str, binary_name: str = "hcli"):
        """Initialize binary updater.

        Args:
            github_url: GitHub repository URL (e.g., https://github.com/owner/repo)
            binary_name: Name of the binary executable
        """
        self.github_url = github_url
        self.owner, self.repo = self._parse_github_url(github_url)
        self.binary_name = binary_name
        self.current_executable = self._get_current_executable()

    def _parse_github_url(self, github_url: str) -> tuple[str, str]:
        """Parse owner/repo from GitHub URL."""
        if "github.com/" in github_url:
            parts = github_url.split("github.com/")[1].rstrip("/").split("/")
            return parts[0], parts[1]
        else:
            # Fallback
            return "HexRaysSA", "ida-hcli"

    async def get_latest_version(self, include_prereleases: bool = False) -> Optional[Version]:
        """Get the latest version from GitHub releases.

        Args:
            include_prereleases: Include pre-release versions

        Returns:
            Latest version or None if not found
        """
        try:
            async with GitHubAPI() as github:
                release = await github.get_latest_release(self.owner, self.repo, include_prereleases)
                return release.version if release else None
        except Exception:
            return None

    def _get_current_executable(self) -> Optional[Path]:
        """Get the path to the current executable."""
        if not is_binary():
            return None

        try:
            if hasattr(sys, "_MEIPASS"):
                # PyInstaller
                executable = Path(sys.executable)
            else:
                # Other freezing tools
                executable = Path(sys.argv[0])

            return executable.resolve()
        except Exception:
            return None

    async def check_for_updates(
        self, include_prereleases: bool = False, force_check: bool = False
    ) -> Optional[GitHubRelease]:
        """Check if updates are available.

        Args:
            include_prereleases: Include pre-release versions
            force_check: Skip frozen check (for testing/development)

        Returns:
            GitHubRelease if update available, None otherwise
        """
        async with GitHubAPI() as github:
            release = await github.get_latest_release(self.owner, self.repo, include_prereleases)
        if not release or not release.version:
            return None

        from hcli import __version__

        if compare_versions(__version__, release.version):
            return release

        return None

    async def download_update(self, release: GitHubRelease, target_dir: Optional[Path] = None) -> Path:
        """Download the binary for the current platform.

        Args:
            release: GitHub release to download
            target_dir: Directory to download to (defaults to temp)

        Returns:
            Path to downloaded binary
        """
        asset = release.get_binary_asset(self.binary_name)
        if not asset:
            available_assets = [a["name"] for a in release.assets]
            raise UpdateError(
                f"No compatible binary found for {platform.system()}-{platform.machine()}. "
                f"Available assets: {', '.join(available_assets)}"
            )

        if target_dir is None:
            target_dir = Path(tempfile.mkdtemp(prefix="hcli_update_"))
        else:
            target_dir.mkdir(parents=True, exist_ok=True)

        # Determine target filename
        asset_name = asset["name"]
        target_file = target_dir / asset_name

        console.print(f"Downloading {asset_name}...")

        async with GitHubAPI() as github:
            downloaded_path = await github.download_asset(asset=asset, target_path=target_file, show_progress=True)

        return Path(downloaded_path)

    def _backup_current_executable(self) -> Optional[Path]:
        """Create backup of current executable.

        Returns:
            Path to backup file or None if backup failed
        """
        if not self.current_executable or not self.current_executable.exists():
            return None

        try:
            backup_path = self.current_executable.with_suffix(self.current_executable.suffix + ".backup")
            shutil.copy2(self.current_executable, backup_path)
            return backup_path
        except Exception as e:
            console.print(f"[yellow]Warning: Could not create backup: {e}[/yellow]")
            return None

    def _make_executable(self, path: Path) -> None:
        """Make file executable on Unix-like systems."""
        if platform.system() != "Windows":
            current_mode = path.stat().st_mode
            path.chmod(current_mode | stat.S_IEXEC)

    async def perform_update(self, release: GitHubRelease, restart_after_update: bool = False) -> bool:
        """Perform the binary update.

        Args:
            release: GitHub release to update to
            restart_after_update: Whether to restart after update

        Returns:
            True if update successful, False otherwise
        """
        console.print(f"[bold]Updating to version {release.tag_name}...[/bold]")

        # Download new binary
        downloaded_binary = await self.download_update(release)

        if not self.current_executable:
            raise UpdateError("Cannot determine current executable path")

        try:
            # Create backup
            backup_path = self._backup_current_executable()

            # Make downloaded binary executable
            self._make_executable(downloaded_binary)

            # Atomic replacement (as atomic as possible)
            temp_path = self.current_executable.with_suffix(".tmp")

            # Copy new binary to temp location
            shutil.copy2(downloaded_binary, temp_path)
            self._make_executable(temp_path)

            # Replace current executable
            if platform.system() == "Windows":
                # On Windows, we might need to handle locked files
                try:
                    self.current_executable.unlink()
                    temp_path.rename(self.current_executable)
                except OSError:
                    # If file is locked, we can use a delayed replacement
                    delayed_path = self.current_executable.with_suffix(".delayed")
                    temp_path.rename(delayed_path)
                    console.print("[yellow]Update will complete after next restart (executable was in use)[/yellow]")
                    return True
            else:
                # On Unix, atomic rename
                temp_path.rename(self.current_executable)

            # Cleanup
            try:
                downloaded_binary.unlink()
                if downloaded_binary.parent.name.startswith("hcli_update_"):
                    downloaded_binary.parent.rmdir()
            except Exception:
                pass

            console.print("[green]✓ Update completed successfully![/green]")

            if restart_after_update:
                console.print("Restarting application...")
                os.execv(str(self.current_executable), sys.argv)

            return True

        except Exception as e:
            # Restore from backup if possible
            if backup_path and backup_path.exists():
                try:
                    shutil.copy2(backup_path, self.current_executable)
                    console.print("[yellow]Restored from backup[/yellow]")
                except Exception:
                    pass

            raise UpdateError(f"Update failed: {e}")

        finally:
            # Cleanup backup
            if backup_path and backup_path.exists():
                try:
                    backup_path.unlink()
                except Exception:
                    pass

    async def update_if_available(self, include_prereleases: bool = False, restart_after_update: bool = False) -> bool:
        """Check for and perform update if available.

        Args:
            include_prereleases: Include pre-release versions
            restart_after_update: Whether to restart after update

        Returns:
            True if update was performed, False otherwise
        """
        release = await self.check_for_updates(include_prereleases)
        if not release:
            console.print("[green]Already using the latest version[/green]")
            return False

        console.print(f"Update available: {release.tag_name}")
        return await self.perform_update(release, restart_after_update)


def get_default_updater() -> BinaryUpdater:
    """Get the default updater for this application."""
    return BinaryUpdater(ENV.HCLI_GITHUB_URL, ENV.HCLI_BINARY_NAME)
