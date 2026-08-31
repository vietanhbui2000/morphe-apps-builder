#!/usr/bin/env python3
"""
Direct URL Downloader: downloads directly from explicit HTTP/HTTPS links.
"""

from pathlib import Path
from typing import Optional

from core.http import http_client
from core.logger import log_info, log_warn, log_error
from downloaders.base import BaseDownloader

class DirectDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "direct"

    def get_versions(self, url: str) -> list[str]:
        # Direct URLs don't have dynamic version listing
        return []

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path
    ) -> Optional[Path]:
        formatted_url = url.replace("{version}", version).replace("{arch}", arch)
        ext = ".apkm" if ".apkm" in formatted_url else (".xapk" if ".xapk" in formatted_url else ".apk")
        dest_file = output_path.parent / f"{output_path.name}{ext}"

        log_info(f"[Direct] Downloading from {formatted_url}...", indent=2)
        if http_client.download_file(formatted_url, dest_file):
            return dest_file

        return None
