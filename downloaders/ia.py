#!/usr/bin/env python3
"""
Internet Archive Downloader (ia_url): parses file listings and downloads matched APKs.
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

class IADownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "ia"

    @property
    def display_name(self) -> str:
        return "Internet Archive"

    def _extract_links(self, html: str) -> list[str]:
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            return [a["href"] for a in soup.find_all("a", href=True)]
        else:
            return re.findall(r'<a\s+(?:[^>]*?\s+)?href="([^"]*)"', html, re.IGNORECASE)

    def get_versions(self, url: str) -> list[str]:
        html = http_client.get_html(url)
        if not html:
            return []

        versions = []
        for href in self._extract_links(html):
            match = re.search(r"-(\d+(\.\d+)+)-(all|arm64-v8a|arm-v7a|armeabi-v7a|x86_64|x86)", href)
            if match:
                v = match.group(1)
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
        log_info(f"[Internet Archive] Checking files for version {version} ({arch})...", indent=2)
        html = http_client.get_html(url)
        if not html:
            return None

        clean_url = url.rstrip("/")
        file_links = self._extract_links(html)

        target_arch = (arch or "universal").replace(" ", "").lower()
        matched_file = None

        if target_arch in ("universal", ""):
            arch_candidates = ["universal", "all", "noarch"]
        elif target_arch == "all":
            arch_candidates = ["all", "universal", "arm64-v8a", "armeabi-v7a", "arm-v7a", "x86_64", "x86"]
        elif target_arch in ("armeabi-v7a", "arm-v7a"):
            arch_candidates = ["armeabi-v7a", "arm-v7a", "universal", "all"]
        else:
            arch_candidates = [target_arch, "universal", "all"]

        for cand in arch_candidates:
            pattern = re.compile(rf"-{re.escape(version)}-{re.escape(cand)}\.(apk|apkm)$", re.IGNORECASE)
            for link in file_links:
                if pattern.search(link):
                    matched_file = link
                    break
            if matched_file:
                break

        if not matched_file:
            for link in file_links:
                if version in link and (link.endswith(".apk") or link.endswith(".apkm")):
                    matched_file = link
                    break

        if not matched_file:
            log_warn(f"[Internet Archive] File matching version {version} not found", indent=2)
            return None

        file_url = f"{clean_url}/{matched_file}" if not matched_file.startswith("http") else matched_file
        is_bundle = matched_file.endswith(".apkm")
        ext = ".apkm" if is_bundle else ".apk"
        dest_path = output_path.with_suffix(ext)

        log_info(f"[Internet Archive] Downloading payload {matched_file}...", indent=2)
        if http_client.download_file(file_url, dest_path):
            return dest_path

        return None
