#!/usr/bin/env python3
"""
Direct URL Downloader: downloads directly from explicit HTTP/HTTPS links.
"""

from pathlib import Path
from typing import Optional

from core.http import http_client
from core.logger import log_info
from downloaders.base import BaseDownloader

class DirectDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "direct"

    @property
    def display_name(self) -> str:
        return "Direct"

    def get_versions(self, url: str) -> list[str]:
        # Direct URLs don't have dynamic version listing
        return []

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path,
        app_id: str = ""
    ) -> Optional[Path]:
        formatted_url = url.replace("{version}", version).replace("{arch}", arch)
        ext = ".apkm" if ".apkm" in formatted_url else (".xapk" if ".xapk" in formatted_url else ".apk")
        dest_path = output_path.with_suffix(ext)

        log_info(f"[Direct] Downloading from {formatted_url}...", indent=2)
        if http_client.download_file(formatted_url, dest_path):
            return dest_path

        return None
