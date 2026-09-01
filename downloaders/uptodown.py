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
from core.logger import log_info, log_warn
from downloaders.base import BaseDownloader

class UptodownDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "uptodown"

    @property
    def display_name(self) -> str:
        return "Uptodown"

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
            if app_name_elem:
                data_code = app_name_elem.get("data-code")
            if not data_code:
                for elem in soup.select("[data-code], [data-appid], [data-app-id], [data-item-id]"):
                    val = elem.get("data-code") or elem.get("data-appid") or elem.get("data-app-id") or elem.get("data-item-id")
                    if val and val.isdigit():
                        data_code = val
                        break

        if not data_code:
            match = re.search(r'data-(?:code|appid|app-id|item-id)="(\d+)"', html)
            if match:
                data_code = match.group(1)
            else:
                match = re.search(r'["\'](?:item_id|app_id|data-code)["\']\s*[:=]\s*["\']?(\d+)', html)
                if match:
                    data_code = match.group(1)

        if not data_code:
            return None

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
        output_path: Path,
        app_id: str = ""
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
            btn = detail_soup.select_one("#detail-download-button") or detail_soup.select_one("button.download")
            if btn:
                data_url = btn.get("data-url")

        if not data_url or data_url == "apps":
            match = re.search(r'id=["\']detail-download-button["\'][^>]*data-url=["\']([^"\']+)["\']', detail_html, re.I) or \
                    re.search(r'data-url=["\']([^"\']+)["\'][^>]*id=["\']detail-download-button["\']', detail_html, re.I) or \
                    re.search(r'class=["\'][^"\']*download[^"\']*["\'][^>]*data-url=["\']([^"\']+)["\']', detail_html, re.I)
            if match:
                data_url = match.group(1)

        if not data_url or data_url == "apps":
            log_warn("[Uptodown] Could not resolve direct download data-url", indent=2)
            return None

        if data_url.startswith("http://") or data_url.startswith("https://"):
            final_dl_url = data_url
        elif data_url.startswith("/"):
            final_dl_url = f"https://dw.uptodown.com{data_url}"
        else:
            final_dl_url = f"https://dw.uptodown.com/dwn/{data_url}"

        ext = ".xapk" if is_bundle else ".apk"
        dest_path = output_path.with_suffix(ext)

        log_info(f"[Uptodown] Downloading payload to {dest_path.name}...", indent=2)
        if http_client.download_file(final_dl_url, dest_path, referer=detail_url):
            return dest_path

        return None
