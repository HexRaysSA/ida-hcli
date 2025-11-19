from __future__ import annotations

import re

import questionary
import rich_click as click
from questionary import Choice

from hcli.commands.common import safe_ask_async
from hcli.lib.api.asset import Asset, TreeNode
from hcli.lib.api.asset import asset as asset_api
from hcli.lib.api.common import get_api_client
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console
from hcli.lib.constants import cli
from hcli.lib.util.io import get_tag_os


class BackNavigationResult:
    """Special result class to indicate backspace navigation."""

    pass


BACK_NAVIGATION = BackNavigationResult()


async def select_asset(nodes: list[TreeNode], current_path: str = "") -> Asset | None:
    """Alternative traverse using questionary.select with hierarchical navigation."""

    async def _traverse_recursive(current_nodes: list[TreeNode], path_stack: list[str]) -> Asset | None:
        # Get folders and files at current level
        folders = [node for node in current_nodes if node.type == "folder" and node.children]
        files = [node for node in current_nodes if node.type == "file"]

        # Build choices using questionary Choice objects
        choices = []

        # Add "Go back" option if not at root - create a special TreeNode for this
        if path_stack:
            dummy_asset = Asset(filename="", key="")
            back_node = TreeNode(name="← Go back", type="back", asset=dummy_asset)
            choices.append(Choice("← Go back", value=back_node))

        # Add folders
        for folder in folders:
            choices.append(Choice(f"📁 {folder.name}", value=folder))

        # Add files
        for file in files:
            # Get display info from asset metadata if available
            display_name = file.name
            if file.asset and file.asset.metadata and "operating_system" in file.asset.metadata:
                metadata_name = file.asset.metadata.get("name", file.name)
                display_name = f"{metadata_name} ({file.asset.key.split('/')[-1]})"
            choices.append(Choice(f"📄 {display_name}", value=file))

        if not choices:
            console.print("[red]No items found at this path[/red]")
            return None

        # Show current path
        path_display = "/" + "/".join(path_stack) if path_stack else "/"
        console.print(f"[blue]Current path: {path_display}[/blue]")

        # Get user selection
        selected_node = await safe_ask_async(
            questionary.select(
                "Select an item to navigate or download:",
                choices=choices,
                use_jk_keys=False,
                use_search_filter=True,
                style=cli.SELECT_STYLE,
            )
        )

        if not selected_node:
            return None

        # Handle selection based on node type
        if selected_node.type == "back":
            # Go back one level by removing last item from path stack
            return await _traverse_recursive(_get_nodes_at_path(nodes, path_stack[:-1]), path_stack[:-1])
        elif selected_node.type == "folder":
            # Navigate into folder
            if selected_node.children:
                new_path_stack = path_stack + [selected_node.name]
                return await _traverse_recursive(selected_node.children, new_path_stack)
            return None
        elif selected_node.type == "file":
            # File selected - return the asset key
            return selected_node.asset if selected_node.asset else None

        return None

    def _get_nodes_at_path(root_nodes: list[TreeNode], path_stack: list[str]) -> list[TreeNode]:
        """Helper to get nodes at a specific path in the tree."""
        current_nodes = root_nodes
        for path_part in path_stack:
            # Find the folder with matching name
            folder = next((node for node in current_nodes if node.type == "folder" and node.name == path_part), None)
            if not folder or not folder.children:
                return []
            current_nodes = folder.children
        return current_nodes

    return await _traverse_recursive(nodes, [])


def collect_all_assets(nodes: list[TreeNode]) -> list[Asset]:
    """Recursively collect all assets from the tree nodes."""
    assets = []

    for node in nodes:
        if node.type == "file" and node.asset:
            assets.append(node.asset)
        elif node.type == "folder" and node.children:
            assets.extend(collect_all_assets(node.children))

    return assets


def filter_assets_by_pattern(assets: list[Asset], pattern: str) -> list[Asset]:
    """Filter assets by regex pattern matching their keys."""
    try:
        compiled_pattern = re.compile(pattern, re.IGNORECASE)
        return [asset for asset in assets if compiled_pattern.search(asset.key)]
    except re.error as e:
        console.print(f"[red]Invalid regex pattern: {e}[/red]")
        return []


def validate_pattern_for_direct_mode(ctx, param, value):
    mode = ctx.params.get("mode")
    if mode == "direct" and not value:
        raise click.BadParameter('--pattern is required when --mode is "direct"')
    return value


async def resolve_tag(tag_spec: str) -> str | None:
    """Resolve a tag specification (e.g., 'ida:latest' or 'ida:9.2') to an asset key.

    Args:
        tag_spec: Tag specification in format 'name' or 'name:version'

    Returns:
        The resolved asset key, or None if tag not found
    """
    try:
        tags = await asset_api.get_tags()

        # Try exact match first
        for tag in tags:
            if tag.tag == tag_spec:
                return tag.key

        # If no exact match, try case-insensitive
        tag_spec_lower = tag_spec.lower()
        for tag in tags:
            if tag.tag.lower() == tag_spec_lower:
                return tag.key

        return None
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to resolve tag: {e}[/yellow]")
        return None


def is_tag_format(key: str) -> bool:
    """Check if the key looks like a tag format (contains ':' and no '/')."""
    return ":" in key and "/" not in key


def normalize_tag_with_os(tag_spec: str) -> str:
    """Normalize a tag by adding the current OS if not present.

    Args:
        tag_spec: Tag specification like 'ida:latest' or 'ida:9.2' or 'ida:latest:armmac'

    Returns:
        Tag with OS appended if needed, e.g., 'ida:latest:armmac'
    """
    parts = tag_spec.split(":")

    # If tag already has 3 parts (category:version:os), return as-is
    if len(parts) >= 3:
        return tag_spec

    # If tag has 2 parts (category:version), append current OS
    if len(parts) == 2:
        return f"{tag_spec}:{get_tag_os()}"

    # Otherwise return as-is
    return tag_spec


@auth_command()
@click.option("-f", "--force", is_flag=True, help="Skip cache")
@click.option("--mode", "mode", default="interactive", help="One of interactive or direct")
@click.option("--output-dir", "output_dir", default="./", help="Output path")
@click.option(
    "--pattern", "pattern", default=None, help="Pattern to search for assets", callback=validate_pattern_for_direct_mode
)
@click.option("--list-tags", is_flag=True, help="List all available download tags and exit")
@click.argument("key", required=False)
@async_command
async def download(
    force: bool = False,
    output_dir: str = "./",
    mode: str = "interactive",
    pattern: str | None = None,
    key: str | None = None,
    list_tags: bool = False,
) -> None:
    """Download IDA binaries, SDKs, and utilities.

    KEY: The asset key for direct download eg. release/9.1/ida-pro/ida-pro_91_x64linux.run (optional)

    Can also be a tag in one of these formats:
    - 'category:version' (e.g., 'ida:latest' or 'ida-pro:9.2') - OS is auto-detected
    - 'category:version:os' (e.g., 'ida:latest:armmac') - explicit OS specification

    \b
    When using direct mode, a pattern is required.

    """
    try:
        # Handle --list-tags flag
        if list_tags:
            console.print("[yellow]Fetching available tags...[/yellow]")
            tags = await asset_api.get_tags()

            if not tags:
                console.print("[yellow]No tags available[/yellow]")
                return

            # Sort tags alphabetically by tag name
            sorted_tags = sorted(tags, key=lambda t: t.tag)

            console.print(f"\n[bold]Available Download Tags ({len(tags)} total):[/bold]\n")
            console.print(f"[bold]{'Tag':<45} {'Name':<40} {'Asset Key'}[/bold]")
            console.print("─" * 150)

            for tag in sorted_tags:
                console.print(f"[green]{tag.tag:<45}[/green] [cyan]{tag.description:<40}[/cyan] [blue]{tag.key}[/blue]")

            console.print()
            console.print(f"[blue]Detected current platform: {get_tag_os()}[/blue]")
            console.print(
                "[grey69]Use 'category:version' to auto-detect OS, or 'category:version:os' for explicit OS[/grey69]"
            )
            return

        if pattern:
            mode = "direct"

        if key:
            # Check if key is a tag format and resolve it
            if is_tag_format(key):
                console.print(f"[yellow]Resolving tag: {key}...[/yellow]")

                # Normalize tag by adding current OS if needed
                normalized_tag = normalize_tag_with_os(key)
                if normalized_tag != key:
                    console.print(f"[blue]Normalized to: {normalized_tag} (current platform)[/blue]")

                resolved_key = await resolve_tag(normalized_tag)
                if resolved_key:
                    console.print(f"[green]Tag resolved to: {resolved_key}[/green]")
                    key = resolved_key
                else:
                    console.print(f"[red]Tag '{normalized_tag}' not found[/red]")
                    available_tags = await asset_api.get_tags()
                    if available_tags:
                        console.print("[yellow]Available tags:[/yellow]")
                        for tag in available_tags[:10]:  # Show first 10
                            console.print(f"  • {tag.tag}")
                        if len(available_tags) > 10:
                            console.print(f"  ... and {len(available_tags) - 10} more")
                    return

            selected_keys = [key]
        else:
            console.print("[yellow]Fetching available downloads...[/yellow]")

            if mode == "direct" and pattern:
                # Get downloads from API
                assets = await asset_api.get_files("installers")

                filtered_assets = filter_assets_by_pattern(assets.items, pattern)

                if not filtered_assets:
                    console.print(f"[red]No assets found matching pattern: {pattern}[/red]")
                    return

                console.print(f"[green]Found {len(filtered_assets)} assets matching pattern:[/green]")
                for asset in filtered_assets:
                    console.print(f"  • {asset.key}")

                selected_keys = [asset.key for asset in filtered_assets]
            else:
                # Get downloads from API
                installer_assets = await asset_api.get_files_tree("installers")

                # Interactive navigation
                selected_asset = await select_asset(installer_assets, "")

                if not selected_asset:
                    console.print("[yellow]Download cancelled[/yellow]")
                    return

                selected_keys = [selected_asset.key]

        # Download files
        client = await get_api_client()
        downloaded_files = []

        for selected_key in selected_keys:
            console.print(f"[yellow]Getting download URL for: {selected_key}[/yellow]")
            try:
                download_asset: Asset | None = await asset_api.get_file("installers", selected_key)
                if not download_asset:
                    console.print(f"[red]Asset '{selected_key}' not found[/red]")
                    continue
            except Exception as e:
                console.print(f"[red]Failed to get download URL for {selected_key}: {e}[/red]")
                continue

            if not download_asset.url:
                console.print(f"[red]Error: No download URL available for asset {selected_key}[/red]")
                continue

            # Download the file
            console.print(f"[yellow]Starting download of {selected_key}...[/yellow]")
            try:
                target_path = await client.download_file(
                    download_asset.url, target_dir=output_dir, force=force, auth=True, asset_key=selected_key
                )
                downloaded_files.append(target_path)
                console.print(f"[green]Download complete! File saved to: {target_path}[/green]")
            except Exception as e:
                console.print(f"[red]Failed to download {selected_key}: {e}[/red]")
                continue

        if downloaded_files:
            console.print(f"[green]Successfully downloaded {len(downloaded_files)} file(s)[/green]")
        else:
            console.print("[red]No files were downloaded[/red]")
            raise ValueError("no files downloaded")

    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise
