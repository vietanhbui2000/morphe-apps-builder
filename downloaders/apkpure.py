#!/usr/bin/env python3
"""
APKPure Downloader: scrapes APKPure version pages and downloads APK / XAPK.
Supports BeautifulSoup if installed, with regex fallback.
"""

import re
from pathlib import Path
from typing import Optional

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from core.http import http_client
from core.logger import log_info, log_warn
from downloaders.base import BaseDownloader

class APKPureDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "apkpure"

    @property
    def display_name(self) -> str:
        return "APKPure"

    def get_versions(self, url: str) -> list[str]:
        clean_url = url.rstrip("/")
        if not clean_url.endswith("/versions"):
            versions_url = f"{clean_url}/versions"
        else:
            versions_url = clean_url

        html = http_client.get_html(versions_url)
        if not html:
            return []

        versions = []
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.select("div.ver-top a, ul.ver-wrap li a"):
                ver_text = tag.get_text(strip=True)
                match = re.search(r"(\d+(\.\d+)+)", ver_text)
                if match:
                    v = match.group(1)
                    if v not in versions:
                        versions.append(v)
        else:
            matches = re.findall(r'/version/(\d+(\.\d+)+)', html)
            for m in matches:
                v = m[0]
                if v not in versions:
                    versions.append(v)

        return versions

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path,
        app_id: str = ""
    ) -> Optional[Path]:
        clean_url = url.rstrip("/")
        if clean_url.endswith("/versions") or clean_url.endswith("/download"):
            clean_url = clean_url.rsplit("/", 1)[0]

        dl_page_url = f"{clean_url}/{version}" if version else f"{clean_url}/download"
        log_info(f"[APKPure] Fetching download page {dl_page_url}...", indent=2)

        html = http_client.get_html(dl_page_url)
        if not html:
            return None

        dl_url = None
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            dl_btn = soup.select_one("a#download_link") or soup.select_one("a.download_btn")
            if dl_btn and dl_btn.get("href"):
                dl_url = dl_btn["href"]

        if not dl_url:
            match = re.search(r'href="(https://[^"]*apkpure[^"]*download[^"]*)"', html)
            dl_url = match.group(1) if match else None

        if not dl_url:
            log_warn("[APKPure] Could not resolve download link", indent=2)
            return None

        is_bundle = "xapk" in dl_url.lower() or "xapk" in html.lower()
        ext = ".xapk" if is_bundle else ".apk"
        dest_path = output_path.with_suffix(ext)

        log_info(f"[APKPure] Downloading payload to {dest_path.name}...", indent=2)
        if http_client.download_file(dl_url, dest_path, referer=dl_page_url):
            return dest_path

        return None
