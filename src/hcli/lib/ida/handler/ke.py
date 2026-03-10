"""KE URL handler — downloads resources from KE servers and launches IDA."""

from __future__ import annotations

from urllib.parse import ParseResult


class KEURLHandler:
    """Handler for KE URLs: ``ida://host/api/v1/buckets/{bucket}/resources/{key}``.

    Delegates the actual download-and-launch logic to ``hcli.lib.ida.ke``.
    """

    def matches(self, parsed: ParseResult) -> bool:
        return "/api/v1/buckets/" in parsed.path

    def handle(
        self,
        uri: str,
        parsed: ParseResult,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        from hcli.lib.ida.ke import _ke_download_and_launch

        _ke_download_and_launch(uri, parsed, no_launch, timeout, skip_analysis)
