#!/usr/bin/env python3
"""
Morphe Apps Builder — Main CLI Orchestrator.
Supports unified all-in-one builds as well as decoupled --download-only and --patch-only workflows.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.apk import merge_bundle, sign_apk, ensure_apk_editor, ensure_keystore
from core.config import load_config
from core.github import github_client
from core.logger import (
    log_info,
    log_success,
    log_warn,
    log_error,
    log_stage,
    log_app_banner,
    group_start,
    group_end,
    Colors
)
from core.models import AppConfig, BuildResult, GeneralConfig
from downloaders import get_download_sources_for_app
from patchers.morphe import morphe_patcher

ROOT_DIR = Path(__file__).resolve().parent
TEMP_DIR = ROOT_DIR / "temp"
OUTPUT_DIR = ROOT_DIR / "output"
DOWNLOADS_DIR = TEMP_DIR / "downloads"
APKS_DIR = DOWNLOADS_DIR / "apks"
CLI_DIR = DOWNLOADS_DIR / "cli"
PATCHES_DIR = DOWNLOADS_DIR / "patches"
MANIFEST_PATH = DOWNLOADS_DIR / "targets_manifest.json"


def clean_workspace():
    """Remove temporary and build output files."""
    log_info("Cleaning temporary and output artifacts...")
    for directory in (TEMP_DIR, OUTPUT_DIR):
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
    release_md_path = ROOT_DIR / "RELEASE.md"
    if release_md_path.is_file():
        release_md_path.unlink(missing_ok=True)
    log_success("Workspace cleaned.")


def _extract_source_tag_from_release_md(source: str, release_text: str) -> str:
    """Extract tag for a CLI or patches repository from previous RELEASE.md."""
    if not release_text:
        return ""
    pattern = rf"{re.escape(source)}[\s:]+\[?([a-zA-Z0-9._-]+)\]?"
    match = re.search(pattern, release_text)
    return match.group(1) if match else ""


def check_updates(config_path: Path) -> int:
    """Check for upstream CLI and patches updates across all enabled apps."""
    general, apps = load_config(config_path)
    enabled_apps = [a for a in apps if a.enabled]

    log_stage("Checking for Upstream Updates")

    # CLI sources first, then patches sources, deduplicated and order-preserving
    all_sources = list(dict.fromkeys(
        [a.cli_source for a in enabled_apps] + [a.patches_source for a in enabled_apps]
    ))
    total_checks = len(all_sources)

    release_md_path = ROOT_DIR / "RELEASE.md"
    prev_release_text = release_md_path.read_text(encoding="utf-8") if release_md_path.is_file() else ""

    source_current_tags: Dict[str, str] = {}
    source_latest_tags: Dict[str, str] = {}
    source_has_update: Dict[str, bool] = {}

    for idx, source in enumerate(all_sources, 1):
        group_start(f"Check [{idx}/{total_checks}]: {source}")
        current_tag = _extract_source_tag_from_release_md(source, prev_release_text)
        source_current_tags[source] = current_tag

        log_info(f"Current: {current_tag or 'none'}")

        latest_tag = ""
        try:
            rel = github_client.get_release(source, "latest")
            if rel:
                latest_tag = rel.get("tag_name", "")
        except Exception as e:
            log_warn(f"Failed to fetch latest release for {source}: {e}")

        source_latest_tags[source] = latest_tag
        log_info(f"Latest: {latest_tag or 'none'}")

        has_update = bool(latest_tag and (not current_tag or current_tag != latest_tag))
        source_has_update[source] = has_update

        if has_update:
            log_success("Updates available.")
        else:
            log_info("Up to date.")
        group_end()

    # Determine which apps need to be built
    apps_to_build: List[str] = []
    app_summary_rows: List[Tuple[str, str]] = []

    for app in enabled_apps:
        cli_cur = source_current_tags.get(app.cli_source, "")
        cli_lat = source_latest_tags.get(app.cli_source, "")
        cli_up = source_has_update.get(app.cli_source, False)

        pat_cur = source_current_tags.get(app.patches_source, "")
        pat_lat = source_latest_tags.get(app.patches_source, "")
        pat_up = source_has_update.get(app.patches_source, False)

        if cli_up or pat_up or not prev_release_text:
            apps_to_build.append(app.name)
            reasons = []
            if cli_up:
                reasons.append(f"{app.cli_source} {cli_lat or cli_cur}")
            if pat_up:
                reasons.append(f"{app.patches_source} {pat_lat or pat_cur}")
            if not reasons:
                reasons.append(f"{app.cli_source} {cli_lat or cli_cur} + {app.patches_source} {pat_lat or pat_cur}")
            app_summary_rows.append((app.name, " + ".join(reasons)))

    print("=" * 70)
    print(f"{Colors.BOLD}CHECK SUMMARY{Colors.RESET}")
    print("=" * 70)

    if app_summary_rows:
        print("> Sources")
        for source in all_sources:
            cur = source_current_tags.get(source, "")
            lat = source_latest_tags.get(source, "")
            if cur and lat and cur != lat:
                print(f"{source}: {cur} -> {lat}")
            else:
                print(f"{source}: {lat or cur or 'unknown'}")

        print("> Apps")
        for name, reason_str in app_summary_rows:
            icon = f"{Colors.YELLOW}[~]{Colors.RESET}"
            print(f"{icon} {name}: [{reason_str}] > TO BUILD")

        print("SHOULD_BUILD=1")
        print(f"APPS_TO_BUILD={','.join(apps_to_build)}")
    else:
        print("All sources are up to date.")
        print("SHOULD_BUILD=0")
        print("APPS_TO_BUILD=")

    return 0


def resolve_app_version(
    app: AppConfig,
    cli_path: Path,
    patches_path: Path,
    dry_run: bool = False
) -> Optional[str]:
    """Resolve target version for app (from config, patches compatibility list, or fallback)."""
    if app.version != "auto":
        log_info(f"Using explicitly configured version: {app.version}")
        return app.version

    if dry_run:
        return "auto"

    # Try resolving from patch bundle compatibility list
    if cli_path.is_file() and patches_path.is_file():
        resolved = morphe_patcher.get_compatible_version(cli_path, patches_path, app.id)
        if resolved:
            log_success(f"Resolved compatible version from patch bundle: {resolved}")
            return resolved

    # Fallback to scraping first available version from download providers
    log_warn("Version not found in patch bundle. Falling back to latest from sources...")
    sources = get_download_sources_for_app(app)
    for _, downloader, src_url in sources:
        vers = downloader.get_versions(src_url)
        if vers:
            log_success(f"Fallback version resolved from {downloader.display_name}: {vers[0]}")
            return vers[0]

    return None


def download_app_targets(
    app: AppConfig,
    resolved_version: str,
    cli_tag: str,
    cli_path: Path,
    patches_tag: str,
    patches_path: Path,
    dry_run: bool = False
) -> Tuple[List[Dict[str, Any]], List[BuildResult]]:
    """
    Download phase for an app given its pre-resolved version and configured architectures.
    Looks for compatible packages matching app.arch (universal, specific archs, or all).
    If a fat package is downloaded when specific archs are configured, it will be reused
    for extracting only what was asked.
    Returns (list_of_targets, list_of_failures).
    """
    targets: List[Dict[str, Any]] = []
    failures: List[BuildResult] = []

    target_info_base = {
        "name": app.name,
        "id": app.id,
        "version": resolved_version,
        "cli_source": app.cli_source,
        "cli_version": app.cli_version,
        "cli_tag": cli_tag,
        "cli_path": str(cli_path),
        "patches_source": app.patches_source,
        "patches_version": app.patches_version,
        "patches_tag": patches_tag,
        "patches_path": str(patches_path),
    }

    if dry_run:
        if app.arch == ["all"]:
            for a in ("universal", "arm64-v8a", "armeabi-v7a"):
                log_info(f"[DRY-RUN] Would download {app.name} v{resolved_version} ({a})", indent=1)
                targets.append({
                    **target_info_base,
                    "arch": a,
                    "stock_apk_path": str(APKS_DIR / f"{app.id}_{resolved_version}_{a}.apk")
                })
        else:
            for a in app.arch:
                log_info(f"[DRY-RUN] Would download {app.name} v{resolved_version} ({a})", indent=1)
                targets.append({
                    **target_info_base,
                    "arch": a,
                    "stock_apk_path": str(APKS_DIR / f"{app.id}_{resolved_version}_{a}.apk")
                })
        return targets, failures

    sources = get_download_sources_for_app(app)
    if not sources:
        for a in app.arch:
            failures.append(BuildResult(
                name=app.name, id=app.id, version=resolved_version, arch=a,
                success=False, error_message="No download sources configured in config.toml",
                cli_source=app.cli_source, cli_tag=cli_tag,
                patches_source=app.patches_source, patches_tag=patches_tag
            ))
        return targets, failures

    APKS_DIR.mkdir(parents=True, exist_ok=True)

    # Track already downloaded packages for this app: {path: [abis]}
    downloaded_packages: Dict[Path, List[str]] = {}

    def _fetch_package(query_arch: str) -> Optional[Path]:
        stock_apk_base = APKS_DIR / f"{app.id}_{resolved_version}_{query_arch}"
        for provider_name, downloader, src_url in sources:
            log_info(f"Attempting download via {provider_name}...", indent=1)
            try:
                downloaded = downloader.download(
                    url=src_url,
                    version=resolved_version,
                    arch=query_arch,
                    dpi=app.dpi,
                    output_path=stock_apk_base,
                    app_id=app.id
                )
                if downloaded and downloaded.is_file() and downloaded.stat().st_size > 0:
                    log_success(f"Downloaded {downloaded.name} via {provider_name}", indent=1)
                    return downloaded
                else:
                    log_warn(f"Provider {provider_name} returned no file, trying next...", indent=1)
            except Exception as e:
                log_warn(f"Provider {provider_name} failed: {e}", indent=1)
        return None

    if app.arch == ["all"]:
        # 1. Discover all available architecture packages for this app version from sources
        available_archs: List[str] = []
        for provider_name, downloader, src_url in sources:
            archs = downloader.get_available_architectures(src_url, resolved_version)
            if archs:
                available_archs = archs
                log_info(f"Discovered available architecture packages via {provider_name}: {', '.join(archs)}", indent=1)
                break

        # Fallback if source does not expose an architecture catalog: attempt universal
        if not available_archs:
            available_archs = ["universal"]

        # 2. Download and register a target for EVERY available architecture package
        for arch in available_archs:
            safe_arch = arch.replace(" ", "")
            log_stage(f"Downloading {app.name} ({safe_arch})")
            downloaded = _fetch_package(safe_arch)
            if not downloaded:
                failures.append(BuildResult(
                    name=app.name, id=app.id, version=resolved_version, arch=safe_arch,
                    success=False, error_message=f"Failed to download package for {safe_arch}",
                    cli_source=app.cli_source, cli_tag=cli_tag,
                    patches_source=app.patches_source, patches_tag=patches_tag
                ))
                continue

            targets.append({
                **target_info_base,
                "arch": safe_arch,
                "stock_apk_path": str(downloaded)
            })

        return targets, failures

    # Specific configured architectures (e.g. ["universal"] or ["arm64-v8a", "armeabi-v7a"])
    for arch in app.arch:
        log_stage(f"Downloading {app.name} ({arch})")
        downloaded = _fetch_package(arch)
        if not downloaded:
            failures.append(BuildResult(
                name=app.name, id=app.id, version=resolved_version, arch=arch,
                success=False, error_message=f"Architecture '{arch}' not found on download providers",
                cli_source=app.cli_source, cli_tag=cli_tag,
                patches_source=app.patches_source, patches_tag=patches_tag
            ))
            continue

        targets.append({
            **target_info_base,
            "arch": arch,
            "stock_apk_path": str(downloaded)
        })

    return targets, failures


def patch_single_target(
    target_info: Dict[str, Any],
    app: AppConfig,
    general: GeneralConfig,
    dry_run: bool = False
) -> BuildResult:
    """
    Patch and sign phase for a pre-downloaded target.
    """
    name = target_info.get("name", app.name)
    arch = target_info["arch"]
    version = target_info["version"]
    cli_source = target_info.get("cli_source", app.cli_source)
    cli_tag = target_info.get("cli_tag", "")
    cli_path = Path(target_info.get("cli_path", ""))
    patches_source = target_info.get("patches_source", app.patches_source)
    patches_tag = target_info.get("patches_tag", "")
    patches_path = Path(target_info.get("patches_path", ""))

    log_stage(f"Patching {name} v{version} ({arch})")

    if dry_run:
        log_info(f"[DRY-RUN] Would patch {name} v{version} ({arch})", indent=1)
        return BuildResult(
            name=name,
            id=app.id,
            version=version,
            arch=arch,
            success=True,
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    downloaded_apk_path = Path(target_info.get("stock_apk_path", ""))
    if not downloaded_apk_path.is_file():
        return BuildResult(
            name=name,
            id=app.id,
            version=version,
            arch=arch,
            success=False,
            error_message=f"Stock APK not found at {downloaded_apk_path}",
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    # 1. Ensure Keystore & Merge if Bundle
    stock_apk_path: Path
    keystore_path = ROOT_DIR / general.keystore
    ensure_keystore(keystore_path, general.keystore_alias, general.keystore_password)

    if downloaded_apk_path.suffix in (".apkm", ".xapk"):
        merged_apk_path = downloaded_apk_path.with_suffix(".merged.apk")
        if not merged_apk_path.is_file():
            log_info(f"Merging split bundle {downloaded_apk_path.name} to standalone APK...", indent=1)
            if not merge_bundle(
                bundle_path=downloaded_apk_path,
                output_path=merged_apk_path,
                keystore_path=keystore_path,
                keystore_alias=general.keystore_alias,
                keystore_password=general.keystore_password,
            ):
                return BuildResult(
                    name=name,
                    id=app.id,
                    version=version,
                    arch=arch,
                    success=False,
                    error_message="Failed to merge split APK bundle",
                    cli_source=cli_source,
                    cli_tag=cli_tag,
                    patches_source=patches_source,
                    patches_tag=patches_tag,
                )
        stock_apk_path = merged_apk_path
    else:
        stock_apk_path = downloaded_apk_path

    # 2. Patching
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    arch_suffix = "" if arch in ("all", "universal", "") else f"_{arch}"
    final_apk_name = f"{name}_v{version}{arch_suffix}.apk"
    output_path = OUTPUT_DIR / final_apk_name
    temp_patched_path = TEMP_DIR / f"patched_{final_apk_name}"

    log_info(f"Applying patches for {name}...", indent=1)
    patch_success = morphe_patcher.patch(
        cli_path=cli_path,
        patches_path=patches_path,
        stock_apk_path=stock_apk_path,
        output_path=temp_patched_path,
        app_config=app,
        keystore_path=keystore_path,
        keystore_alias=general.keystore_alias,
        keystore_password=general.keystore_password,
    )

    if not patch_success or not temp_patched_path.is_file():
        return BuildResult(
            name=name,
            id=app.id,
            version=version,
            arch=arch,
            success=False,
            error_message="Patching execution failed",
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    # 3. Signing
    log_info("Signing release APK...", indent=1)
    if not sign_apk(
        apk_path=temp_patched_path,
        keystore_path=keystore_path,
        keystore_alias=general.keystore_alias,
        keystore_password=general.keystore_password,
        output_path=output_path,
    ):
        return BuildResult(
            name=name,
            id=app.id,
            version=version,
            arch=arch,
            success=False,
            error_message="Signing release APK failed",
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    log_success(f"Successfully generated: {output_path.name}", indent=1)
    return BuildResult(
        name=name,
        id=app.id,
        version=version,
        arch=arch,
        success=True,
        output_path=output_path,
        cli_source=cli_source,
        cli_tag=cli_tag,
        patches_source=patches_source,
        patches_tag=patches_tag,
    )


def save_manifest(targets: List[Dict[str, Any]]):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(targets, indent=2), encoding="utf-8")
    log_success(f"Saved download targets manifest to {MANIFEST_PATH.name}")


def load_manifest() -> List[Dict[str, Any]]:
    if not MANIFEST_PATH.is_file():
        return []
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _get_github_repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        try:
            out = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True, check=False)
            url = out.stdout.strip()
            if "github.com" in url:
                cleaned = url.split("github.com", 1)[1].lstrip("/:").removesuffix(".git")
                if "/" in cleaned:
                    repo = cleaned
        except Exception:
            pass
    return repo


def _format_version(v: Optional[str]) -> str:
    """Format version string ensuring consistent 'v' prefix, defaulting to 'vauto'."""
    if not v:
        return "vauto"
    return v if v.startswith("v") else f"v{v}"


def write_download_summary(
    download_targets: List[Dict[str, Any]],
    failed_downloads: List[BuildResult]
) -> int:
    """Generate console summary for the download phase."""
    print("=" * 70)
    print(f"{Colors.BOLD}DOWNLOAD SUMMARY{Colors.RESET}")
    print("=" * 70)

    grouped_success: Dict[str, List[Dict[str, Any]]] = {}
    for t in download_targets:
        name = t.get("name", "")
        if name:
            grouped_success.setdefault(name, []).append(t)

    grouped_failed: Dict[str, List[BuildResult]] = {}
    for r in failed_downloads:
        grouped_failed.setdefault(r.name, []).append(r)

    all_app_names = list(dict.fromkeys(list(grouped_success.keys()) + list(grouped_failed.keys())))

    for name in all_app_names:
        app_targets = grouped_success.get(name, [])
        app_failures = grouped_failed.get(name, [])

        v_raw = ""
        if app_targets:
            v_raw = app_targets[0].get("version", "")
        elif app_failures:
            v_raw = app_failures[0].version
        version = _format_version(v_raw)

        total_targets = len(app_targets) + len(app_failures)
        is_multi = total_targets > 1

        if app_targets and not app_failures:
            icon = f"{Colors.GREEN}[✓]{Colors.RESET}"
            if is_multi:
                parts = [f"({t['arch']}) {Path(t['stock_apk_path']).name}" for t in app_targets]
                print(f"{icon} {name}: {version} > {'; '.join(parts)}")
            else:
                print(f"{icon} {name}: {version} > {Path(app_targets[0]['stock_apk_path']).name}")
        elif app_targets and app_failures:
            icon = f"{Colors.YELLOW}[▲]{Colors.RESET}"
            parts = [f"({t['arch']}) {Path(t['stock_apk_path']).name}" for t in app_targets]
            for r in app_failures:
                err = r.error_message or "Download failed"
                parts.append(f"({r.arch}) FAILED {{{err}}}")
            print(f"{icon} {name}: {version} > {'; '.join(parts)}")
        else:
            icon = f"{Colors.RED}[✗]{Colors.RESET}"
            if is_multi:
                parts = [f"({r.arch}) FAILED {{{r.error_message or 'Download failed'}}}" for r in app_failures]
                print(f"{icon} {name}: {version} > {'; '.join(parts)}")
            else:
                err = app_failures[0].error_message if app_failures else "Download failed"
                print(f"{icon} {name}: {version} > FAILED {{{err}}}")

    if download_targets:
        save_manifest(download_targets)

    return 0 if download_targets else 1


def write_patch_summary(results: List[BuildResult]) -> int:
    """Generate console summary and RELEASE.md."""
    print("=" * 70)
    print(f"{Colors.BOLD}PATCH SUMMARY{Colors.RESET}")
    print("=" * 70)

    # Group results by name maintaining order
    grouped: Dict[str, List[BuildResult]] = {}
    for r in results:
        grouped.setdefault(r.name, []).append(r)

    for name, app_results in grouped.items():
        all_success = all(r.success for r in app_results)
        any_success = any(r.success for r in app_results)
        is_multi = len(app_results) > 1

        first_r = app_results[0]
        v_raw = first_r.version
        version = _format_version(v_raw)

        cli_tag = first_r.cli_tag or "latest"
        patches_tag = first_r.patches_tag or "latest"
        recipe_str = f"{first_r.cli_source} {cli_tag} + {first_r.patches_source} {patches_tag}"

        if all_success:
            icon = f"{Colors.GREEN}[✓]{Colors.RESET}"
            if is_multi:
                parts = [
                    f"({r.arch}) {r.output_path.name if r.output_path else f'{r.name}_{version}_{r.arch}.apk'}"
                    for r in app_results
                ]
                print(f"{icon} {name}: {version} [{recipe_str}] > {'; '.join(parts)}")
            else:
                apk_name = first_r.output_path.name if first_r.output_path else f"{first_r.name}_{version}.apk"
                print(f"{icon} {name}: {version} [{recipe_str}] > {apk_name}")
        elif any_success:
            icon = f"{Colors.YELLOW}[▲]{Colors.RESET}"
            parts = []
            for r in app_results:
                if r.success and r.output_path:
                    parts.append(f"({r.arch}) {r.output_path.name}")
                else:
                    err = r.error_message or "Patching failed"
                    parts.append(f"({r.arch}) FAILED {{{err}}}")
            print(f"{icon} {name}: {version} [{recipe_str}] > {'; '.join(parts)}")
        else:
            icon = f"{Colors.RED}[✗]{Colors.RESET}"
            if is_multi:
                parts = [f"({r.arch}) FAILED {{{r.error_message or 'Patching failed'}}}" for r in app_results]
                print(f"{icon} {name}: {version} [{recipe_str}] > {'; '.join(parts)}")
            else:
                err = first_r.error_message or "Patching failed"
                print(f"{icon} {name}: {version} [{recipe_str}] > FAILED {{{err}}}")

    # Write RELEASE.md for GitHub Releases
    release_md_path = ROOT_DIR / "RELEASE.md"
    repo = _get_github_repo()
    release_tag = os.environ.get("RELEASE_TAG", "").strip()

    new_app_lines: Dict[str, str] = {}
    new_source_lines: Dict[str, str] = {}

    for name, app_results in grouped.items():
        success_results = [r for r in app_results if r.success]
        if not success_results:
            continue

        first_r = success_results[0]
        target_links = []
        is_multi = len(success_results) > 1
        for r in success_results:
            version = _format_version(r.version)
            if is_multi or r.arch not in ("all", "universal", ""):
                label = f"{version} ({r.arch})"
            else:
                label = version

            if repo and release_tag:
                apk_name = r.output_path.name if r.output_path else f"{r.name}_{version}{'' if r.arch in ('all', 'universal', '') else f'_{r.arch}'}.apk"
                dl_url = f"https://github.com/{repo}/releases/download/{release_tag}/{apk_name}"
                target_links.append(f"[{label}]({dl_url})")
            else:
                target_links.append(label)

        targets_str = "; ".join(target_links)
        patches_tag = first_r.patches_tag or "latest"
        cli_tag = first_r.cli_tag or "latest"

        # App line format: AppName: [vX.Y.Z](link) [`patches_source patches_tag`]  
        new_app_lines[name] = f"{name}: {targets_str} [`{first_r.patches_source} {patches_tag}`]  "

        # Track sources for bottom section
        cli_url = f"https://github.com/{first_r.cli_source}/releases/tag/{cli_tag}"
        new_source_lines[first_r.cli_source] = f"{first_r.cli_source}: [{cli_tag}]({cli_url})  "

        pat_url = f"https://github.com/{first_r.patches_source}/releases/tag/{patches_tag}"
        new_source_lines[first_r.patches_source] = f"{first_r.patches_source}: [{patches_tag}]({pat_url})  "

    existing_apps: Dict[str, str] = {}
    existing_sources: Dict[str, str] = {}

    if release_md_path.is_file():
        prev_content = release_md_path.read_text(encoding="utf-8")
        if "---" in prev_content:
            apps_part, sources_part = prev_content.split("---", 1)
        else:
            apps_part = prev_content
            sources_part = ""

        for line in apps_part.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith(("#", "ℹ", "└")):
                continue
            if ":" in line_s:
                app_key = line_s.split(":", 1)[0].strip()
                if "(" in app_key or "[" in app_key or "└" in app_key:
                    continue
                existing_apps[app_key] = line_s + "  "

        for line in sources_part.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            if ":" in line_s:
                source_key = line_s.split(":", 1)[0].strip()
                existing_sources[source_key] = line_s + "  "

    existing_apps.update(new_app_lines)
    existing_sources.update(new_source_lines)

    sections = []
    if existing_apps:
        app_lines_str = "\n".join(existing_apps.values())
        sections.append(app_lines_str)
        sections.append("ℹ Install [MicroG ↗](https://github.com/MorpheApp/MicroG-RE/) to enable Google account authentication and services for Morphe apps.")

    if existing_sources:
        source_lines_str = "\n".join(existing_sources.values())
        sections.append(f"---\n\n{source_lines_str}")

    release_md_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    log_success(f"Wrote release notes to {release_md_path.name}")

    return 0 if any(r.success for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Morphe Apps Builder")
    parser.add_argument("-c", "--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("-a", "--app", help="Build or download only a specific app by section name (comma-separated for multiple)")
    parser.add_argument("--download-only", action="store_true", help="Download prebuilts and stock APKs only")
    parser.add_argument("--patch-only", action="store_true", help="Patch and sign pre-downloaded APKs only")
    parser.add_argument("--check-updates", action="store_true", help="Check for patch updates without building")
    parser.add_argument("--clean", action="store_true", help="Clean temp, output, and RELEASE.md artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Inspect execution plan without downloading or patching")
    args = parser.parse_args()

    if args.clean:
        clean_workspace()
        return 0

    config_path = ROOT_DIR / args.config
    if args.check_updates:
        return check_updates(config_path)

    general, apps = load_config(config_path)

    # Build a full map before any filtering so --patch-only can resolve all app names from the manifest
    apps_map = {a.name: a for a in apps}

    # Filter apps if --app specified (supports comma-separated list)
    if args.app:
        target_names = {x.strip().lower() for x in args.app.split(",") if x.strip()}
        apps = [a for a in apps if a.name.lower() in target_names or a.id.lower() in target_names]
        if not apps:
            log_error(f"No matching apps found in config for '{args.app}'")
            return 1

    enabled_apps = [a for a in apps if a.enabled]
    if not enabled_apps:
        log_warn("No enabled apps found in configuration.")
        return 0

    # --------------------------------------------------------------------------
    # PHASE 1: DOWNLOAD PHASE
    # --------------------------------------------------------------------------
    targets_to_patch: List[Dict[str, Any]] = []
    failed_downloads: List[BuildResult] = []

    if not args.patch_only:
        log_stage("Starting Download Phase (Prebuilts & Stock APKs)")
        download_targets: List[Dict[str, Any]] = []

        total_download_tasks = len(enabled_apps) + 1

        # 1. Download Prebuilts (Tools, CLI & Patches)
        group_start(f"Download [1/{total_download_tasks}]: Prebuilts (Tools, CLI & Patches)")
        log_stage("Fetching Prebuilt Tools, CLI & Patches")

        # bin tools
        if not args.dry_run:
            ensure_apk_editor()
        else:
            log_info("[DRY-RUN] Would download APKEditor.jar...", indent=1)

        prebuilts_cache: Dict[Tuple[str, str, str, str], Tuple[str, Optional[Path], str, Optional[Path]]] = {}
        unique_prebuilts = list(dict.fromkeys(
            (app.cli_source, app.cli_version, app.patches_source, app.patches_version)
            for app in enabled_apps
        ))

        for cli_source, cli_version, patches_source, patches_version in unique_prebuilts:
            cli_tag, cli_path, patches_tag, patches_path = github_client.get_prebuilts(
                cli_source=cli_source,
                cli_version=cli_version,
                patches_source=patches_source,
                patches_version=patches_version,
                cli_dir=CLI_DIR,
                patches_dir=PATCHES_DIR
            )
            if not cli_path or not patches_path:
                if args.dry_run:
                    cli_tag = "mock"
                    cli_path = CLI_DIR / "morphe-cli-mock.jar"
                    patches_tag = "mock"
                    patches_path = PATCHES_DIR / "morphe-patches-mock.mpp"
            prebuilts_cache[(cli_source, cli_version, patches_source, patches_version)] = (
                cli_tag, cli_path, patches_tag, patches_path
            )
        group_end()

        # 2. Download Stock APKs for each enabled app
        for app_idx, app in enumerate(enabled_apps, 1):
            task_idx = app_idx + 1
            group_start(f"Download [{task_idx}/{total_download_tasks}]: {app.name}")
            log_app_banner(app_idx, len(enabled_apps), app.name, app.id)

            cli_tag, cli_path, patches_tag, patches_path = prebuilts_cache.get(
                (app.cli_source, app.cli_version, app.patches_source, app.patches_version),
                ("", None, "", None)
            )

            if not cli_path or not patches_path:
                group_end()
                for arch in app.arch:
                    failed_downloads.append(BuildResult(
                        name=app.name,
                        id=app.id,
                        version="unknown",
                        arch=arch,
                        success=False,
                        error_message=f"Failed to fetch CLI/patches ({app.cli_source} / {app.patches_source})",
                        cli_source=app.cli_source,
                        cli_tag=cli_tag,
                        patches_source=app.patches_source,
                        patches_tag=patches_tag,
                    ))
                continue

            resolved_version = resolve_app_version(app, cli_path, patches_path, dry_run=args.dry_run)
            if not resolved_version:
                log_error(f"Could not resolve version for {app.name}", indent=1)
                group_end()
                for arch in app.arch:
                    failed_downloads.append(BuildResult(
                        name=app.name,
                        id=app.id,
                        version="unknown",
                        arch=arch,
                        success=False,
                        error_message="Could not resolve version for app",
                        cli_source=app.cli_source,
                        cli_tag=cli_tag,
                        patches_source=app.patches_source,
                        patches_tag=patches_tag,
                    ))
                continue

            app_targets, app_failures = download_app_targets(
                app=app,
                resolved_version=resolved_version,
                cli_tag=cli_tag,
                cli_path=cli_path,
                patches_tag=patches_tag,
                patches_path=patches_path,
                dry_run=args.dry_run
            )
            download_targets.extend(app_targets)
            failed_downloads.extend(app_failures)

            group_end()

        targets_to_patch = download_targets
        download_exit_code = write_download_summary(download_targets, failed_downloads)

        if args.download_only:
            return download_exit_code

    # --------------------------------------------------------------------------
    # PHASE 2: PATCH & BUILD PHASE
    # --------------------------------------------------------------------------
    if args.patch_only:
        targets_to_patch = load_manifest()
        if not targets_to_patch:
            log_error("No downloaded targets found in manifest. Run with --download-only first or without flags.")
            return 1

    log_stage("Starting Patching & Signing Phase")
    results: List[BuildResult] = []

    # Group targets by app maintaining order
    targets_by_app: Dict[str, List[Dict[str, Any]]] = {}
    for t in targets_to_patch:
        name = t.get("name", "")
        if name:
            targets_by_app.setdefault(name, []).append(t)

    total_patch_apps = len(targets_by_app)
    for app_idx, (name, app_targets) in enumerate(targets_by_app.items(), 1):
        app = apps_map.get(name)
        if not app:
            continue

        group_start(f"Patch [{app_idx}/{total_patch_apps}]: {app.name}")
        log_app_banner(app_idx, total_patch_apps, app.name, app.id)

        for t in app_targets:
            res = patch_single_target(
                target_info=t,
                app=app,
                general=general,
                dry_run=args.dry_run
            )
            results.append(res)

        group_end()

    all_results = failed_downloads + results
    return write_patch_summary(all_results)


if __name__ == "__main__":
    sys.exit(main())
