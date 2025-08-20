from __future__ import annotations

from typing import List, Optional

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


class BackNavigationResult:
    """Special result class to indicate backspace navigation."""

    pass


BACK_NAVIGATION = BackNavigationResult()


async def select_asset(nodes: List[TreeNode], current_path: str = "") -> Optional[Asset]:
    """Alternative traverse using questionary.select with hierarchical navigation."""

    async def _traverse_recursive(current_nodes: List[TreeNode], path_stack: List[str]) -> Optional[Asset]:
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
                display_name = f"{file.asset.metadata['name']} ({file.asset.metadata['operating_system']})"
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

    def _get_nodes_at_path(root_nodes: List[TreeNode], path_stack: List[str]) -> List[TreeNode]:
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


@auth_command()
@click.option("-f", "--force", is_flag=True, help="Skip cache")
@click.option("--output-dir", "output_dir", default="./", help="Output path")
@click.argument("slug", required=False)
@async_command
async def download(
    force: bool = False,
    output_dir: str = "./",
    version_filter: Optional[str] = None,
    latest: bool = False,
    category_filter: Optional[str] = None,
    slug: Optional[str] = None,
) -> None:
    """Download IDA binaries, SDK, utilities and more."""
    try:
        selected_key: Optional[str]

        if slug:
            selected_key = slug
        else:
            # Get downloads from API
            console.print("[yellow]Fetching available downloads...[/yellow]")
            assets = await asset_api.get_files_tree("installers")

            if not assets:
                console.print("[red]No downloads available or unable to fetch downloads[/red]")
                return

            # Interactive navigation
            selected_asset = await select_asset(assets, "")

            if not selected_asset:
                console.print("[yellow]Download cancelled[/yellow]")
                return

            selected_key = selected_asset.key

        # Get download URL
        console.print(f"[yellow]Getting download URL for: {selected_key}[/yellow]")
        try:
            asset = await asset_api.get_file("installers", selected_key)
        except Exception as e:
            console.print(f"[red]Failed to get download URL: {e}[/red]")
            return

        if not asset:
            console.print(f"[red]Asset '{selected_key}' not found[/red]")
            return

        # Download the file
        console.print("[yellow]Starting download...[/yellow]")
        client = await get_api_client()

        if not asset.url:
            console.print("[red]Error: No download URL available for asset[/red]")
            return

        target_path = await client.download_file(asset.url, target_dir=output_dir, force=force, auth=True)

        console.print(f"[green]Download complete! File saved to: {target_path}[/green]")

    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise
