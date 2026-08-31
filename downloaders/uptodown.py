#!/usr/bin/env python3
"""
Uptodown Downloader: uses Uptodown version API and CDN to download APK / XAPK.
Supports BeautifulSoup if installed, with regex fallback.
"""

import json
import re
from pathlib import Path
from typing import Optional

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from core.http import http_client
from core.logger import log_info, log_warn, log_error
from downloaders.base import BaseDownloader

class UptodownDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "uptodown"

    def _get_app_info(self, base_url: str) -> Optional[tuple[str, str]]:
        clean_url = base_url.rstrip("/")
        if clean_url.endswith("/versions"):
            clean_url = clean_url[:-9]
        if clean_url.endswith("/download"):
            clean_url = clean_url[:-9]

        html = http_client.get_html(f"{clean_url}/versions")
        if not html:
            html = http_client.get_html(clean_url)
        if not html:
            return None

        data_code = None
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            app_name_elem = soup.select_one("#detail-app-name")
            data_code = app_name_elem.get("data-code") if app_name_elem else None

        if not data_code:
            match = re.search(r'data-code="(\d+)"', html)
            if match:
                data_code = match.group(1)

        return clean_url, data_code

    def get_versions(self, url: str) -> list[str]:
        info = self._get_app_info(url)
        if not info or not info[1]:
            return []

        clean_url, data_code = info
        versions = []
        for page in range(1, 10):
            api_url = f"{clean_url}/apps/{data_code}/versions/{page}"
            html = http_client.get_html(api_url)
            if not html:
                break
            try:
                data = json.loads(html)
                entries = data.get("data", [])
                if not entries:
                    break
                for entry in entries:
                    v = entry.get("version")
                    if v and v not in versions:
                        versions.append(v)
            except Exception:
                break

        return versions

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path
    ) -> Optional[Path]:
        info = self._get_app_info(url)
        if not info or not info[1]:
            log_warn("[Uptodown] Could not resolve Uptodown data-code", indent=2)
            return None

        clean_url, data_code = info
        log_info(f"[Uptodown] Searching for version {version}...", indent=2)

        version_entry = None
        for page in range(1, 20):
            api_url = f"{clean_url}/apps/{data_code}/versions/{page}"
            resp_text = http_client.get_html(api_url)
            if not resp_text:
                continue
            try:
                data = json.loads(resp_text)
                for entry in data.get("data", []):
                    if entry.get("version") == version:
                        version_entry = entry
                        break
                if version_entry:
                    break
            except Exception:
                break

        if not version_entry:
            log_warn(f"[Uptodown] Version {version} not found", indent=2)
            return None

        is_bundle = (version_entry.get("kindFile") == "xapk")
        version_url_data = version_entry.get("versionURL")
        if isinstance(version_url_data, dict):
            base = version_url_data.get("url", clean_url)
            extra = version_url_data.get("extraURL", "download")
            vid = version_url_data.get("versionID") or version_entry.get("versionID") or version_entry.get("fileID")
            detail_url = f"{base}/{extra}/{vid}"
        elif isinstance(version_url_data, str) and version_url_data:
            detail_url = version_url_data
        else:
            vid = version_entry.get("versionID") or version_entry.get("fileID")
            detail_url = f"{clean_url}/download/{vid}"

        detail_html = http_client.get_html(detail_url)
        if not detail_html:
            return None

        data_url = None
        if HAS_BS4:
            detail_soup = BeautifulSoup(detail_html, "html.parser")
            btn = detail_soup.select_one("#detail-download-button")
            data_url = btn.get("data-url") if btn else None

        if not data_url:
            match = re.search(r'data-url="([^"]+)"', detail_html)
            if match:
                data_url = match.group(1)

        if not data_url:
            log_warn("[Uptodown] Could not resolve direct download data-url", indent=2)
            return None

        final_dl_url = f"https://dw.uptodown.com/dwn/{data_url}"
        ext = ".xapk" if is_bundle else ".apk"
        dest_file = output_path.parent / f"{output_path.name}{ext}"

        log_info(f"[Uptodown] Downloading payload to {dest_file.name}...", indent=2)
        if http_client.download_file(final_dl_url, dest_file, referer=detail_url):
            return dest_file

        return None
