"""File sharing API client."""

from __future__ import annotations  # only needed for Python <3.10

import hashlib
from pathlib import Path
from typing import ForwardRef

import httpx
from pydantic import BaseModel

from hcli.env import ENV
from hcli.lib.util.string import get_email_domain

from .common import get_api_client

SHARED = "shared"
INSTALLERS = "installers"

# Forward reference for TreeNode
TreeNodeType = ForwardRef("TreeNode")


class TreeNode(BaseModel):
    name: str
    type: str = "file"
    children: "list[TreeNode] | None" = None
    asset: Asset


class Asset(BaseModel):
    email: str | None = None
    filename: str
    size: int = 0
    key: str
    code: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    url: str | None = None
    version: int = 0
    metadata: dict | None = None


class PagedAsset(BaseModel):
    """Paged response wrapper."""

    offset: int
    limit: int
    total: int
    items: list[Asset]


class Metadata(BaseModel):
    name: str


class RequiredField(BaseModel):
    description: str
    example: str


class Bucket(BaseModel):
    filename: str
    metadata: Metadata
    requiredMetadata: dict[str, RequiredField]


class PagingFilter(BaseModel):
    """Paging filter parameters."""

    limit: int | None = 1000
    offset: int | None = 0


class UploadResponse(BaseModel):
    """Upload response."""

    bucket: str
    key: str
    version: int
    code: str
    url: str
    download_url: str


class AssetAPI:
    """File sharing API client."""

    async def get_bucket(self, bucket: str):
        client = await get_api_client()
        data = await client.get_json(f"/api/assets/buckets/{bucket}")
        return Bucket(**data)

    async def upload_asset(
        self,
        bucket: str,
        file_path: str,
        allowed_segments: list[str] | None = None,
        allowed_emails: list[str] | None = None,
        metadata: dict | None = None,
        force: bool = False,
        code: str | None = None,
    ) -> UploadResponse:
        """Upload a file for sharing."""
        file_path_obj = Path(file_path)
        filename = file_path_obj.name
        size = file_path_obj.stat().st_size

        # Calculate SHA-256 checksum by streaming the file
        checksum_obj = hashlib.sha256()
        with open(file_path_obj, "rb") as f:
            while chunk := f.read(8192):
                checksum_obj.update(chunk)
        checksum = checksum_obj.hexdigest()

        client = await get_api_client()

        upload_data = {
            "filename": filename,
            "size": size,
            "force": force,
            "status": "active",
            "checksum": checksum,
        }

        # Add optional fields if provided
        if allowed_segments is not None:
            upload_data["allowed_segments"] = allowed_segments
        if allowed_emails is not None:
            upload_data["allowed_emails"] = allowed_emails
        if metadata is not None:
            upload_data["metadata"] = metadata

        if code:
            upload_data["code"] = code

        # ask for an upload url
        response = await client.post_json(f"/api/assets/{bucket}", upload_data)
        upload_url = response.get("url")
        file_code = response.get("code")
        key = response.get("key")
        version = response.get("version")

        if upload_url:
            # Upload the file (without content parameter to use streaming)
            await client.put_file(upload_url, file_path_obj)
            # Confirm upload
            response = await client.post_json(f"/api/assets/{bucket}/{key}", {})

        return UploadResponse(
            bucket=bucket,
            key=key,
            version=version,
            code=file_code,
            url=f"{ENV.HCLI_PORTAL_URL}/share/{file_code}",
            download_url=f"{ENV.HCLI_API_URL}/api/assets/s/{file_code}",
        )

    async def delete_file_by_key(self, bucket: str, key: str) -> None:
        """Delete a file."""
        client = await get_api_client()
        await client.delete_json(f"/api/assets/{bucket}/{key}")

    async def get_shared_file_by_code(self, code: str, version: int = -1) -> Asset | None:
        """Get information about a file."""
        client = await get_api_client()
        try:
            data = await client.get_json(f"/api/assets/s/{code}?version={version}")
            return Asset(**data)
        except httpx.HTTPStatusError:
            return None

    async def get_file_versions(self, bucket: str, code: str) -> Asset:
        """Get all versions of a shared file."""
        client = await get_api_client()
        data = await client.get_json(f"/api/assets/{bucket}/{code}/versions")
        return Asset(**data)

    async def get_files(self, bucket: str, filter_params: PagingFilter | None = None) -> PagedAsset:
        """Get all shared files for the current user."""
        if filter_params is None:
            filter_params = PagingFilter()

        client = await get_api_client()
        data = await client.get_json(
            f"/api/assets/{bucket}?type=file&limit={filter_params.limit}&offset={filter_params.offset}"
        )
        return PagedAsset(**data)

    async def get_file(self, bucket: str, key: str) -> Asset | None:
        client = await get_api_client()
        data = await client.get_json(f"/api/assets/{bucket}/{key}")
        return Asset(**data)

    async def get_files_tree(self, bucket: str, filter_params: PagingFilter | None = None) -> list[TreeNode]:
        """Get all shared files for the current user."""
        if filter_params is None:
            filter_params = PagingFilter()

        client = await get_api_client()
        data = await client.get_json(
            f"/api/assets/{bucket}?type=file&view=tree&limit={filter_params.limit}&offset={filter_params.offset}"
        )
        return [TreeNode(**item) for item in data]


def get_permissions_from_acl_type(acl_type: str, user_email: str) -> dict[str, list[str] | None]:
    if acl_type == "authenticated":
        return {
            "allowed_segments": ["authenticated"],
            "allowed_emails": None,
        }
    elif acl_type == "domain":
        return {
            "allowed_segments": ["authenticated", f"@{get_email_domain(user_email)}"],
            "allowed_emails": None,
        }
    elif acl_type == "private":
        return {
            "allowed_segments": ["authenticated"],
            "allowed_emails": [user_email],
        }
    else:
        return {
            "allowed_segments": [],
            "allowed_emails": [],
        }


# Global instance
asset = AssetAPI()
