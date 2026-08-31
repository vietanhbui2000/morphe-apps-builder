#!/usr/bin/env python3
"""
APKMirror Downloader: scrapes release pages, matches architectures/DPI, and downloads APK/APKM.
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
from core.logger import log_info, log_warn, log_error
from downloaders.base import BaseDownloader

BASE_URL = "https://www.apkmirror.com"

class APKMirrorDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "apkmirror"

    def get_versions(self, url: str) -> list[str]:
        clean_url = url.rstrip("/")
        if not clean_url.startswith("http"):
            clean_url = f"{BASE_URL}/apk/{clean_url}"

        html = http_client.get_html(clean_url)
        if not html:
            return []

        versions = []
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for row in soup.select("h5.appRowTitle a.fontBlack"):
                text = row.get_text(strip=True)
                if re.search(r"\b(beta|alpha|preview)\b", text, re.IGNORECASE):
                    continue
                ver_matches = re.findall(r"(\d+(?:\.\d+)+)", text)
                if ver_matches:
                    v = ver_matches[-1]
                    if v not in versions:
                        versions.append(v)
        else:
            matches = re.findall(r'<h5[^>]*class="[^"]*appRowTitle[^"]*"[^>]*>.*?href="([^"]*)".*?>([^<]*)<', html, re.DOTALL)
            for href, text in matches:
                if re.search(r"\b(beta|alpha|preview)\b", text, re.IGNORECASE):
                    continue
                ver_matches = re.findall(r"(\d+(?:\.\d+)+)", text)
                if ver_matches:
                    v = ver_matches[-1]
                    if v not in versions:
                        versions.append(v)

        return versions

    def _extract_version_link(self, html: str, target_ver: str) -> Optional[str]:
        if not html:
            return None
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for row in soup.select("h5.appRowTitle a.fontBlack"):
                text = row.get_text(strip=True)
                if re.search(r"\b(beta|alpha|preview)\b", text, re.IGNORECASE):
                    continue
                if target_ver in text:
                    href = row.get("href")
                    if href:
                        return f"{BASE_URL}{href}" if href.startswith("/") else href
        else:
            matches = re.findall(r'href="(/apk/[^"]*)"[^>]*>([^<]*)<', html)
            for href, text in matches:
                if re.search(r"\b(beta|alpha|preview)\b", text, re.IGNORECASE):
                    continue
                if target_ver in text:
                    return f"{BASE_URL}{href}" if href.startswith("/") else href
        return None

    def _find_version_page(self, base_url: str, version: str) -> Optional[str]:
        clean_url = base_url.rstrip("/")
        app_name_match = re.search(r"/apk/([^/]+)/([^/]+)", clean_url)
        app_slug = app_name_match.group(2) if app_name_match else ""
        ver_slug = version.replace(".", "-").replace(" ", "-")
        ver_parts = ver_slug.split("-")

        # Strategy 1: Direct Slug
        if app_slug:
            candidate_url = f"{clean_url}/{app_slug}-{ver_slug}-release/"
            html = http_client.get_html(candidate_url)
            if html and "404 Whoops" not in html and "Page Not Found" not in html and ("downloadButton" in html or "table-row" in html):
                return candidate_url

        # Strategy 2: URL Pattern Substitution
        page1_html = http_client.get_html(clean_url)
        if page1_html and app_slug:
            app_path = re.search(r'/apk/[^/]+/[^/]+', clean_url)
            app_path_prefix = app_path.group(0) if app_path else ""
            # Match only release index links belonging to this app's own path
            pattern = re.escape(app_path_prefix) + r'/([^/"]*-release/)' if app_path_prefix else r'href="(/apk/[^/]+/[^/]+/[^/"]*-release/)"'
            existing_links = re.findall(pattern, page1_html)
            suffixes_tried: set = set()
            for link in existing_links:
                link_full = f"{BASE_URL}{link}" if link.startswith("/") else link
                slug_m = re.search(r'/([^/]+)-release/$', link_full)
                if not slug_m:
                    continue
                release_slug = slug_m.group(1)
                # Strip the app_slug prefix to isolate the numeric portion
                prefix = f"{app_slug}-"
                if release_slug.startswith(prefix):
                    numeric_part = release_slug[len(prefix):]
                else:
                    m = re.match(r'^[a-z-]*?(\d.*)$', release_slug)
                    numeric_part = m.group(1) if m else release_slug
                segments = numeric_part.split("-")
                n = len(ver_parts)
                # Only learn suffix from links whose version segment count >= our target
                if len(segments) >= n and all(s.isdigit() for s in segments[:n]):
                    suffix = ("-" + "-".join(segments[n:])) if len(segments) > n else ""
                    if suffix not in suffixes_tried:
                        suffixes_tried.add(suffix)
                        mutated_url = f"{clean_url}/{app_slug}-{ver_slug}{suffix}-release/"
                        html = http_client.get_html(mutated_url)
                        if html and "404 Whoops" not in html and "Page Not Found" not in html and ("downloadButton" in html or "table-row" in html):
                            return mutated_url

        # Strategy 3: Category & Query Search
        if app_slug:
            search_urls = [
                f"{BASE_URL}/uploads/?appcategory={app_slug}&q={version}",
                f"{BASE_URL}/?post_type=app_release&searchtype=apk&s={app_slug}+{version}"
            ]
            for s_url in search_urls:
                html_search = http_client.get_html(s_url)
                found = self._extract_version_link(html_search, version)
                if found:
                    return found

        # Strategy 4: Pagination Crawl (Pages 1 to 10)
        if page1_html:
            found = self._extract_version_link(page1_html, version)
            if found:
                return found
        for page_num in range(2, 11):
            p_url = f"{clean_url}/page/{page_num}/"
            html_page = http_client.get_html(p_url)
            if not html_page:
                break
            found = self._extract_version_link(html_page, version)
            if found:
                return found

        return None

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path
    ) -> Optional[Path]:
        log_info(f"[APKMirror] Resolving release page for version {version}...", indent=2)
        version_page_url = self._find_version_page(url, version)
        if not version_page_url:
            log_warn(f"[APKMirror] Version {version} page not found", indent=2)
            return None

        html = http_client.get_html(version_page_url)
        if not html:
            return None

        variants = []
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for row in soup.select("div.table-row"):
                badge = row.select_one("span.apkm-badge")
                link = row.select_one("a.accent_color")
                if not badge or not link:
                    continue

                v_type = badge.get_text(strip=True).upper()
                v_href = link.get("href")
                if not v_href:
                    continue

                cells = [c.get_text(strip=True) for c in row.select("div.table-cell")]
                row_text = " ".join(cells).lower()

                variants.append({
                    "type": v_type,
                    "href": f"{BASE_URL}{v_href}" if v_href.startswith("/") else v_href,
                    "text": row_text
                })

            if not variants:
                dl_btn = soup.select_one("a.downloadButton")
                if dl_btn and dl_btn.get("href"):
                    variants.append({
                        "type": "APK",
                        "href": f"{BASE_URL}{dl_btn['href']}" if dl_btn['href'].startswith("/") else dl_btn['href'],
                        "text": "direct"
                    })
        else:
            # Regex fallback
            rows = html.split('<div class="table-row')
            for r in rows[1:]:
                badge_m = re.search(r'class="[^"]*apkm-badge[^"]*"[^>]*>\s*([^<\s]+)\s*<', r)
                link_m = re.search(r'class="[^"]*accent_color[^"]*"[^>]*href="([^"]+)"', r)
                if badge_m and link_m:
                    v_type = badge_m.group(1).upper()
                    v_href = link_m.group(1)
                    variants.append({
                        "type": v_type,
                        "href": f"{BASE_URL}{v_href}" if v_href.startswith("/") else v_href,
                        "text": r.lower()
                    })

        if not variants:
            log_warn("[APKMirror] No variants found on version page", indent=2)
            return None

        target_arch = arch.lower()
        matched_variant = None
        valid_archs = ["universal", "noarch"]
        if target_arch not in ("all", ""):
            valid_archs.append(target_arch)
            if target_arch == "arm-v7a":
                valid_archs.append("armeabi-v7a")
            elif target_arch == "armeabi-v7a":
                valid_archs.append("arm-v7a")

        target_dpi = dpi.lower() if dpi else ""
        for pref_type in ("APK", "BUNDLE"):
            for v in variants:
                if v["type"] != pref_type:
                    continue
                v_text = v["text"]
                arch_match = target_arch in ("all", "") or any(a in v_text for a in valid_archs)
                dpi_match = not target_dpi or target_dpi in v_text
                if arch_match and dpi_match:
                    matched_variant = v
                    break
            if matched_variant:
                break

        if not matched_variant:
            matched_variant = variants[0]

        is_bundle = (matched_variant["type"] == "BUNDLE")
        ext = ".apkm" if is_bundle else ".apk"
        dest_file = output_path.parent / f"{output_path.name}{ext}"

        log_info(f"[APKMirror] Fetching variant download page ({matched_variant['type']})...", indent=2)
        v_html = http_client.get_html(matched_variant["href"])
        if not v_html:
            return None

        step_href = None
        if HAS_BS4:
            v_soup = BeautifulSoup(v_html, "html.parser")
            dl_step_btn = v_soup.select_one("a.downloadButton") or v_soup.select_one("a.btn")
            if dl_step_btn and dl_step_btn.get("href"):
                step_href = dl_step_btn["href"]
        else:
            step_m = re.search(r'<a[^>]*class="[^"]*(?:downloadButton|btn)[^"]*"[^>]*href="([^"]+)"', v_html)
            if step_m:
                step_href = step_m.group(1)

        if not step_href:
            log_warn("[APKMirror] Could not find download button on variant page", indent=2)
            return None

        dl_step_url = f"{BASE_URL}{step_href}" if step_href.startswith("/") else step_href
        step_html = http_client.get_html(dl_step_url)
        if not step_html:
            return None

        final_href = None
        if HAS_BS4:
            step_soup = BeautifulSoup(step_html, "html.parser")
            final_link_elem = (
                step_soup.select_one("a#download-link") or
                step_soup.select_one("span > a[rel='nofollow']") or
                step_soup.select_one("a.downloadButton")
            )
            if final_link_elem and final_link_elem.get("href"):
                final_href = final_link_elem["href"]
        else:
            final_m = re.search(r'<a[^>]*id="download-link"[^>]*href="([^"]+)"', step_html) or re.search(r'href="([^"]+)"[^>]*rel="nofollow"', step_html)
            if final_m:
                final_href = final_m.group(1)

        if not final_href:
            log_warn("[APKMirror] Could not find final direct download link", indent=2)
            return None

        final_url = f"{BASE_URL}{final_href}" if final_href.startswith("/") else final_href

        log_info(f"[APKMirror] Downloading payload to {dest_file.name}...", indent=2)
        if http_client.download_file(final_url, dest_file, referer=dl_step_url):
            return dest_file

        return None
