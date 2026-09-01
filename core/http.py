#!/usr/bin/env python3
"""
HTTP Client with session management, browser headers, and transparent FlareSolverr / Cloudflare bypass fallback.
Supports requests if installed, otherwise seamlessly falls back to Python stdlib urllib.
"""

import http.cookiejar
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from core.logger import log_info, log_warn

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

class HttpClient:
    def __init__(self):
        self.user_agent = DEFAULT_USER_AGENT
        self.flaresolverr_url = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
        self.cfb_url = os.environ.get("CFB_URL", "http://localhost:8000/html")
        self._flaresolverr_available: Optional[bool] = None
        self._cfb_available: Optional[bool] = None

        if HAS_REQUESTS:
            self.session = requests.Session()
        else:
            self.cookie_jar = http.cookiejar.CookieJar()
            self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def _check_cf_solvers(self) -> None:
        if self._flaresolverr_available is None:
            try:
                base_fs = self.flaresolverr_url.replace("/v1", "")
                req = urllib.request.Request(base_fs, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    self._flaresolverr_available = (resp.status == 200)
            except Exception:
                self._flaresolverr_available = False

        if self._cfb_available is None:
            try:
                base_cfb = self.cfb_url.replace("/html", "")
                req = urllib.request.Request(base_cfb, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    self._cfb_available = (resp.status == 200)
            except Exception:
                self._cfb_available = False

    def _is_cloudflare_challenge(self, status_code: int, text: str) -> bool:
        if status_code in (403, 503):
            return True
        challenge_markers = [
            "Just a moment...",
            "cf-chl-bypass",
            "Checking your browser",
            "cloudflare-static",
            "<title>Access denied</title>"
        ]
        return any(marker in text for marker in challenge_markers)

    def _solve_with_flaresolverr(self, url: str) -> Optional[str]:
        try:
            payload = json.dumps({
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 60000
            }).encode("utf-8")
            req = urllib.request.Request(
                self.flaresolverr_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=70) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if data.get("status") == "ok":
                        solution = data.get("solution", {})
                        if solution.get("userAgent"):
                            self.user_agent = solution["userAgent"]
                        if solution.get("cookies"):
                            for c in solution["cookies"]:
                                if HAS_REQUESTS and isinstance(c, dict) and c.get("name"):
                                    self.session.cookies.set(c["name"], c.get("value", ""), domain=c.get("domain", ""))
                        return solution.get("response", "")
        except Exception as e:
            log_warn(f"FlareSolverr solve error: {e}", indent=2)
        return None

    def get_flaresolverr_cookies(self, url: str) -> Tuple[Dict[str, str], str]:
        """Query FlareSolverr and return (cookies_dict, user_agent)."""
        payload = json.dumps({
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60000,
            "returnOnlyCookies": True
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                self.flaresolverr_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=70) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if data.get("status") == "ok":
                        solution = data.get("solution", {})
                        cookies = {c["name"]: c["value"] for c in solution.get("cookies", []) if "name" in c and "value" in c}
                        ua = solution.get("userAgent", "")
                        return cookies, ua
        except Exception as e:
            log_warn(f"FlareSolverr cookie extraction error: {e}", indent=2)
        return {}, ""

    def _solve_with_cfb(self, url: str) -> Optional[str]:
        try:
            target = f"{self.cfb_url}?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(target, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status == 200:
                    ua_header = resp.headers.get("x-cf-bypasser-user-agent")
                    if ua_header:
                        self.user_agent = ua_header
                    return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log_warn(f"CloudflareBypass solve error: {e}", indent=2)
        return None

    def get_html(self, url: str, headers: Optional[dict] = None, allow_cf_bypass: bool = True) -> Optional[str]:
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)

        # 1. Direct GET
        try:
            if HAS_REQUESTS:
                resp = self.session.get(url, headers=req_headers, timeout=20)
                if resp.status_code == 200 and not self._is_cloudflare_challenge(resp.status_code, resp.text):
                    return resp.text
            else:
                req = urllib.request.Request(url, headers=req_headers)
                with self.opener.open(req, timeout=20) as resp:
                    text = resp.read().decode("utf-8", errors="ignore")
                    if resp.status == 200 and not self._is_cloudflare_challenge(resp.status, text):
                        return text
        except Exception as e:
            log_warn(f"Direct GET request failed ({url}): {e}", indent=2)

        if not allow_cf_bypass:
            return None

        # 2. FlareSolverr / CFB
        self._check_cf_solvers()
        if self._flaresolverr_available:
            log_info("Cloudflare challenge detected: Resolving via FlareSolverr...", indent=2)
            html = self._solve_with_flaresolverr(url)
            if html:
                return html

        if self._cfb_available:
            log_info("Resolving via CloudflareBypass...", indent=2)
            html = self._solve_with_cfb(url)
            if html:
                return html

        log_warn(f"Failed to fetch HTML for: {url}", indent=2)
        return None

    def download_file(self, url: str, output_path: Path, headers: Optional[dict] = None, referer: Optional[str] = None) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(f"{output_path.suffix}.tmp")

        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/octet-stream,*/*",
        }
        if referer:
            req_headers["Referer"] = referer
        if headers:
            req_headers.update(headers)

        for attempt in range(1, 4):
            try:
                if HAS_REQUESTS:
                    with self.session.get(url, headers=req_headers, stream=True, timeout=60) as r:
                        if r.status_code not in (200, 206):
                            log_warn(f"Download HTTP {r.status_code} for: {url}", indent=2)
                            time.sleep(2)
                            continue

                        with open(tmp_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                else:
                    req = urllib.request.Request(url, headers=req_headers)
                    with self.opener.open(req, timeout=60) as resp:
                        if resp.status not in (200, 206):
                            log_warn(f"Download HTTP {resp.status} for: {url}", indent=2)
                            time.sleep(2)
                            continue
                        with open(tmp_path, "wb") as f:
                            while True:
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)

                if tmp_path.is_file() and tmp_path.stat().st_size > 0:
                    shutil.move(str(tmp_path), str(output_path))
                    return True
            except Exception as e:
                log_warn(f"Download attempt {attempt}/3 failed: {e}", indent=2)
                time.sleep(2)
            finally:
                if tmp_path.is_file():
                    tmp_path.unlink(missing_ok=True)

        return False

# Global client
http_client = HttpClient()
