#!/usr/bin/env python3
"""
GitHub API client for discovering and downloading releases and prebuilt binaries.
Supports requests if available, with urllib fallback.
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from core.logger import log_info, log_warn, log_error

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

class GitHubClient:
    def __init__(self):
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Morphe-Apps-Builder",
        }
        if GITHUB_TOKEN:
            self.headers["Authorization"] = f"token {GITHUB_TOKEN}"

        if HAS_REQUESTS:
            self.session = requests.Session()
            self.session.headers.update(self.headers)

    def _get_json(self, url: str) -> Optional[Any]:
        try:
            if HAS_REQUESTS:
                r = self.session.get(url, timeout=15)
                if r.status_code == 200:
                    return r.json()
            else:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return None

    def get_release(self, repo: str, tag: str = "latest") -> Optional[dict]:
        api_url = f"https://api.github.com/repos/{repo}/releases"
        if tag == "latest":
            data = self._get_json(f"{api_url}/latest")
            if data:
                return data
            releases = self._get_json(api_url)
            if releases and isinstance(releases, list):
                return releases[0]
        elif tag in ("prerelease", "dev"):
            releases = self._get_json(api_url)
            if releases and isinstance(releases, list):
                for rel in releases:
                    if rel.get("prerelease") or "-dev" in rel.get("tag_name", ""):
                        return rel
                return releases[0]
        else:
            tag_name = tag if tag.startswith("v") else tag
            data = self._get_json(f"{api_url}/tags/{tag_name}")
            if data:
                return data
            if not tag.startswith("v"):
                return self._get_json(f"{api_url}/tags/v{tag}")

        return None

    def download_asset(self, asset: dict, output_path: Path) -> bool:
        if output_path.is_file() and output_path.stat().st_size > 0:
            return True

        output_path.parent.mkdir(parents=True, exist_ok=True)
        download_url = asset.get("browser_download_url") or asset.get("url")
        if not download_url:
            return False

        headers = dict(self.headers)
        if "api.github.com" in download_url and GITHUB_TOKEN:
            headers["Accept"] = "application/octet-stream"

        log_info(f"Downloading {asset.get('name')} from GitHub...", indent=2)
        tmp_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        try:
            if HAS_REQUESTS:
                with self.session.get(download_url, headers=headers, stream=True, timeout=60) as r:
                    if r.status_code == 200:
                        with open(tmp_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                        tmp_path.rename(output_path)
                        return True
            else:
                req = urllib.request.Request(download_url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    if resp.status == 200:
                        with open(tmp_path, "wb") as f:
                            while True:
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                        tmp_path.rename(output_path)
                        return True
        except Exception as e:
            log_warn(f"Failed to download asset {asset.get('name')}: {e}", indent=2)
        finally:
            if tmp_path.is_file():
                tmp_path.unlink(missing_ok=True)

        return False

    def get_prebuilts(
        self,
        cli_repo: str,
        cli_tag: str,
        patches_repo: str,
        patches_tag: str,
        cli_dir: Path,
        patches_dir: Optional[Path] = None
    ) -> Tuple[Optional[Path], Optional[Path], str, str]:
        p_dir = patches_dir or cli_dir
        cli_dir.mkdir(parents=True, exist_ok=True)
        p_dir.mkdir(parents=True, exist_ok=True)

        # 1. CLI
        cli_rel = self.get_release(cli_repo, cli_tag)
        if not cli_rel:
            log_error(f"Could not find CLI release for {cli_repo} ({cli_tag})", indent=2)
            return None, None, "", ""

        cli_tag_name = cli_rel.get("tag_name", "")
        cli_asset = None
        for a in cli_rel.get("assets", []):
            name = a.get("name", "")
            if name.endswith(".jar") and not name.endswith("-sources.jar") and not name.endswith("-javadoc.jar"):
                if "desktop" in name or "cli" in name:
                    cli_asset = a
                    break
        if not cli_asset and cli_rel.get("assets"):
            cli_asset = cli_rel["assets"][0]

        if not cli_asset:
            log_error(f"No JAR asset found in CLI release {cli_repo} {cli_tag_name}", indent=2)
            return None, None, "", ""

        safe_cli_repo = cli_repo.replace("/", "_")
        cli_jar_path = cli_dir / f"{safe_cli_repo}_{cli_tag_name}.jar"
        if not self.download_asset(cli_asset, cli_jar_path):
            return None, None, "", ""

        # 2. Patches
        patches_rel = self.get_release(patches_repo, patches_tag)
        if not patches_rel:
            log_error(f"Could not find patches release for {patches_repo} ({patches_tag})", indent=2)
            return None, None, "", ""

        patches_tag_name = patches_rel.get("tag_name", "")
        patches_asset = None
        for a in patches_rel.get("assets", []):
            name = a.get("name", "")
            if name.endswith(".mpp") or (name.endswith(".jar") and "patches" in name):
                if not name.endswith(".asc") and not name.endswith(".json"):
                    patches_asset = a
                    break

        if not patches_asset and patches_rel.get("assets"):
            patches_asset = patches_rel["assets"][0]

        if not patches_asset:
            log_error(f"No patch (.mpp/.jar) asset found in {patches_repo} {patches_tag_name}", indent=2)
            return None, None, "", ""

        ext = ".mpp" if patches_asset["name"].endswith(".mpp") else ".jar"
        safe_patches_repo = patches_repo.replace("/", "_")
        patches_path = p_dir / f"{safe_patches_repo}_{patches_tag_name}{ext}"
        if not self.download_asset(patches_asset, patches_path):
            return None, None, "", ""

        return cli_jar_path, patches_path, cli_tag_name, patches_tag_name

github_client = GitHubClient()
