#!/usr/bin/env python3
"""
Morphe Apps Builder — Main CLI Orchestrator.
Supports unified all-in-one builds as well as decoupled --download-only and --patch-only workflows.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.apk import merge_bundle, strip_archs, sign_apk, ensure_apk_editor, ensure_keystore
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
MANIFEST_FILE = DOWNLOADS_DIR / "targets_manifest.json"


def clean_workspace():
    log_info("Cleaning temporary and output artifacts...")
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    release_md = ROOT_DIR / "release.md"
    if release_md.is_file():
        release_md.unlink()
    log_success("Workspace cleaned.")


def check_updates(config_path: Path) -> int:
    """Check if any upstream patch releases have newer versions compared to release.md."""
    log_stage("Checking for upstream updates")
    general, apps = load_config(config_path)

    release_md_path = ROOT_DIR / "release.md"
    existing_log = ""
    if release_md_path.is_file():
        existing_log = release_md_path.read_text(encoding="utf-8")

    updates_found = False
    checked_sources = set()

    for app in apps:
        if not app.enabled:
            continue

        source_key = f"{app.patches_source}:{app.patches_version}"
        if source_key in checked_sources:
            continue
        checked_sources.add(source_key)

        log_info(f"Checking {app.name} patch source: {app.patches_source} ({app.patches_version})...")
        rel = github_client.get_release(app.patches_source, app.patches_version)
        if not rel:
            log_warn(f"Could not fetch release info for {app.patches_source}")
            continue

        tag_name = rel.get("tag_name", "")
        if tag_name and tag_name not in existing_log:
            log_success(f"New patch release detected: {app.patches_source} -> {tag_name}")
            updates_found = True

    if updates_found or not existing_log:
        print("SHOULD_BUILD=1")
        return 0
    else:
        print("SHOULD_BUILD=0")
        return 0


def resolve_app_version(app: AppConfig, cli_jar: Path, patch_file: Path, dry_run: bool = False) -> Optional[str]:
    """Resolve compatible version for an app."""
    if app.version != "auto":
        return app.version

    if dry_run and (not cli_jar.is_file() or not patch_file.is_file()):
        return "auto"

    log_info("Resolving highest compatible version from patch bundle...", indent=1)
    detected_ver = morphe_patcher.get_compatible_version(cli_jar, patch_file, app.id)
    if detected_ver:
        log_success(f"Compatible version resolved: {detected_ver}", indent=1)
        return detected_ver

    log_warn("Could not detect compatible version from patch bundle, attempting latest from downloaders...", indent=1)
    sources = get_download_sources_for_app(app)
    for _, dl_inst, src_url in sources:
        vers = dl_inst.get_versions(src_url)
        if vers:
            log_success(f"Fallback version resolved from {dl_inst.name}: {vers[0]}", indent=1)
            return vers[0]

    return "auto" if dry_run else None


def download_single_target(
    app: AppConfig,
    arch: str,
    cli_jar: Path,
    patch_file: Path,
    cli_tag: str,
    patch_tag: str,
    dry_run: bool = False
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Download phase for a single architecture target.
    Returns (target_info_dict, error_message).
    """
    log_stage(f"Downloading {app.app_name} [{arch}]")

    resolved_version = resolve_app_version(app, cli_jar, patch_file, dry_run=dry_run)
    if not resolved_version:
        return None, "Could not resolve version for app"

    if dry_run:
        log_info(f"[DRY-RUN] Would download {app.app_name} v{resolved_version} ({arch})", indent=1)
        return {
            "app": app.name,
            "id": app.id,
            "version": resolved_version,
            "arch": arch,
            "stock_apk": "",
            "cli_source": app.cli_source,
            "cli_version": cli_tag,
            "cli_file": str(cli_jar),
            "patches_source": app.patches_source,
            "patches_version": patch_tag,
            "patches_file": str(patch_file),
        }, None

    sources = get_download_sources_for_app(app)
    if not sources:
        return None, "No download sources configured in config.toml"

    APKS_DIR.mkdir(parents=True, exist_ok=True)
    stock_apk_base = APKS_DIR / f"{app.id}_{resolved_version}_{arch}"
    downloaded_file: Optional[Path] = None

    for provider_name, dl_inst, src_url in sources:
        log_info(f"Attempting download via {provider_name}...", indent=1)
        try:
            downloaded_file = dl_inst.download(
                url=src_url,
                version=resolved_version,
                arch=arch,
                dpi=app.dpi,
                output_path=stock_apk_base
            )
            if downloaded_file and downloaded_file.is_file() and downloaded_file.stat().st_size > 0:
                log_success(f"Downloaded {downloaded_file.name} via {provider_name}", indent=1)
                break
            else:
                log_warn(f"Provider {provider_name} returned no file, trying next...", indent=1)
        except Exception as e:
            log_warn(f"Provider {provider_name} failed: {e}", indent=1)

    if not downloaded_file or not downloaded_file.is_file():
        return None, "All download providers failed"

    target_info = {
        "app": app.name,
        "id": app.id,
        "version": resolved_version,
        "arch": arch,
        "stock_apk": str(downloaded_file),
        "cli_source": app.cli_source,
        "cli_version": cli_tag,
        "cli_file": str(cli_jar),
        "patches_source": app.patches_source,
        "patches_version": patch_tag,
        "patches_file": str(patch_file),
    }
    return target_info, None


def patch_single_target(
    target_info: Dict[str, Any],
    app: AppConfig,
    general: GeneralConfig,
    dry_run: bool = False
) -> BuildResult:
    """
    Patch and sign phase for a pre-downloaded target.
    """
    app_name = target_info.get("app", target_info.get("app_name", app.name))
    arch = target_info["arch"]
    version = target_info["version"]
    cli_jar = Path(target_info.get("cli_file", target_info.get("cli_jar", "")))
    patch_file = Path(target_info.get("patches_file", target_info.get("patch_file", "")))

    cli_source = target_info.get("cli_source", app.cli_source)
    cli_tag = target_info.get("cli_version", target_info.get("cli_tag", ""))
    patches_source = target_info.get("patches_source", app.patches_source)
    patches_tag = target_info.get("patches_version", target_info.get("patches_tag", target_info.get("patch_tag", "")))

    log_stage(f"Patching {app_name} [{arch}] v{version}")

    if dry_run:
        log_info(f"[DRY-RUN] Would patch {app_name} v{version} ({arch})", indent=1)
        return BuildResult(
            app_name=app_name,
            id=app.id,
            version=version,
            arch=arch,
            success=True,
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    downloaded_file = Path(target_info["stock_apk"])
    if not downloaded_file.is_file():
        return BuildResult(
            app_name=app_name,
            id=app.id,
            version=version,
            arch=arch,
            success=False,
            error_message=f"Stock APK not found at {downloaded_file}",
            cli_source=cli_source,
            cli_tag=cli_tag,
            patches_source=patches_source,
            patches_tag=patches_tag,
        )

    # 1. Ensure Keystore & Merge if Bundle
    stock_apk: Path
    stock_apk_base = APKS_DIR / f"{app.id}_{version}_{arch}"
    keystore_path = ROOT_DIR / general.keystore
    ensure_keystore(keystore_path, general.keystore_alias, general.keystore_password)

    if downloaded_file.suffix in (".apkm", ".xapk") or "bundle" in downloaded_file.name:
        merged_apk = stock_apk_base.parent / f"{stock_apk_base.name}.merged.apk"
        log_info(f"Merging split bundle {downloaded_file.name} to standalone APK...", indent=1)
        if not merge_bundle(downloaded_file, merged_apk, keystore_path, general.keystore_password, general.keystore_alias):
            return BuildResult(
                app_name=app_name,
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
        stock_apk = merged_apk
    else:
        stock_apk = downloaded_file

    # 2. Patching
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    final_apk_name = f"{app_name}_v{version}_{arch}.apk"
    output_apk = OUTPUT_DIR / final_apk_name
    temp_patched = TEMP_DIR / f"patched_{final_apk_name}"

    log_info(f"Applying patches for {app_name}...", indent=1)
    patch_success = morphe_patcher.patch(
        cli_jar=cli_jar,
        patch_files=[patch_file],
        stock_apk=stock_apk,
        output_apk=temp_patched,
        app_config=app,
        keystore_path=keystore_path,
        keystore_password=general.keystore_password,
        keystore_alias=general.keystore_alias
    )

    if not patch_success or not temp_patched.is_file():
        return BuildResult(
            app_name=app_name,
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

    # 3. Native Architecture Stripping & Signing
    if arch not in ("all", "universal", ""):
        log_info(f"Filtering native libraries for {arch}...", indent=1)
        stripped_apk = TEMP_DIR / f"stripped_{final_apk_name}"
        if strip_archs(temp_patched, arch, stripped_apk):
            temp_patched = stripped_apk

    log_info("Signing release APK...", indent=1)
    if not sign_apk(temp_patched, keystore_path, general.keystore_password, general.keystore_alias, output_apk):
        return BuildResult(
            app_name=app_name,
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

    log_success(f"Successfully generated: {output_apk.name}", indent=1)
    return BuildResult(
        app_name=app_name,
        id=app.id,
        version=version,
        arch=arch,
        success=True,
        output_apk=output_apk,
        cli_source=cli_source,
        cli_tag=cli_tag,
        patches_source=patches_source,
        patches_tag=patches_tag,
    )


def save_manifest(targets: List[Dict[str, Any]]):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(targets, indent=2), encoding="utf-8")
    log_success(f"Saved download targets manifest to {MANIFEST_FILE.name}")


def load_manifest() -> List[Dict[str, Any]]:
    if not MANIFEST_FILE.is_file():
        return []
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_build_summary(results: List[BuildResult]) -> int:
    """Generate console summary and build.md."""
    print("\n" + "=" * 70)
    print(f"{Colors.BOLD}BUILD SUMMARY{Colors.RESET}")
    print("=" * 70)

    successful_builds = [r for r in results if r.success]
    failed_builds = [r for r in results if not r.success]

    for r in results:
        status_icon = f"{Colors.GREEN}✓ SUCCESS{Colors.RESET}" if r.success else f"{Colors.RED}✗ FAILED{Colors.RESET}"
        detail = f"-> {r.output_apk.name}" if r.output_apk else f"({r.error_message})"
        print(f"[{status_icon}] {r.app_name} [{r.arch}] v{r.version} {detail}")

    # Write release.md for GitHub Releases
    release_md = ROOT_DIR / "release.md"
    sections = []

    if successful_builds:
        app_blocks = []
        for r in successful_builds:
            arch_str = "" if r.arch in ("all", "universal", "") else f" ({r.arch})"
            header = f"{r.app_name}{arch_str}: {r.version}  "
            sub = f"└ {r.cli_source} {r.cli_tag} + {r.patches_source} {r.patches_tag}".strip()
            app_blocks.append(f"{header}\n{sub}")
        sections.append("\n\n".join(app_blocks))

        # Collect unique CLI sources first, then Patch sources
        cli_sources = []
        seen_cli = set()
        for r in successful_builds:
            if r.cli_source:
                key = (r.cli_source, r.cli_tag)
                if key not in seen_cli:
                    seen_cli.add(key)
                    cli_sources.append(key)

        patch_sources = []
        seen_patches = set()
        for r in successful_builds:
            if r.patches_source:
                key = (r.patches_source, r.patches_tag)
                if key not in seen_patches:
                    seen_patches.add(key)
                    patch_sources.append(key)

        all_sources = cli_sources + patch_sources
        if all_sources:
            source_lines = ["Sources  "]
            for repo, tag in all_sources:
                tag_display = f"[{tag}](https://github.com/{repo}/releases/tag/{tag})" if tag else ""
                source_lines.append(f"[{repo}](https://github.com/{repo}) {tag_display}  ")
            sections.append("\n".join(source_lines))

        sections.append("ℹ Install [MicroG](https://github.com/MorpheApp/MicroG-RE/) to enable Google account authentication and services for Morphe apps.")

    release_md.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    log_success(f"Wrote release notes to {release_md.name}")

    return 0 if successful_builds else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Morphe Apps Builder")
    parser.add_argument("-c", "--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("-a", "--app", help="Build or download only a specific app by section name")
    parser.add_argument("--download-only", action="store_true", help="Download prebuilts and stock APKs only")
    parser.add_argument("--patch-only", action="store_true", help="Patch and sign pre-downloaded APKs only")
    parser.add_argument("--check-updates", action="store_true", help="Check for patch updates without building")
    parser.add_argument("--clean", action="store_true", help="Clean temp, output, and release.md artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Inspect execution plan without downloading or patching")
    args = parser.parse_args()

    if args.clean:
        clean_workspace()
        return 0

    config_path = ROOT_DIR / args.config
    if args.check_updates:
        return check_updates(config_path)

    general, apps = load_config(config_path)

    # Filter apps if --app specified
    if args.app:
        apps = [a for a in apps if a.name.lower() == args.app.lower() or a.id.lower() == args.app.lower()]
        if not apps:
            log_error(f"No matching app found in config for '{args.app}'")
            return 1

    enabled_apps = [a for a in apps if a.enabled]
    if not enabled_apps:
        log_warn("No enabled apps found in configuration.")
        return 0

    apps_map = {a.name: a for a in apps}

    # --------------------------------------------------------------------------
    # PHASE 1: DOWNLOAD PHASE
    # --------------------------------------------------------------------------
    targets_to_patch: List[Dict[str, Any]] = []

    if not args.patch_only:
        log_stage("Starting Download Phase (Prebuilts & Stock APKs)")
        download_targets: List[Dict[str, Any]] = []
        failed_downloads: List[BuildResult] = []

        for index, app in enumerate(enabled_apps, 1):
            group_start(f"Download: {app.name} ({index}/{len(enabled_apps)})")
            log_app_banner(index, len(enabled_apps), app.app_name, app.id)

            # Fetch Prebuilt CLI & Patches
            cli_jar, patch_file, cli_tag, patch_tag = github_client.get_prebuilts(
                cli_repo=app.cli_source,
                cli_tag=app.cli_version,
                patches_repo=app.patches_source,
                patches_tag=app.patches_version,
                cli_dir=CLI_DIR,
                patches_dir=PATCHES_DIR
            )

            if not cli_jar or not patch_file:
                if args.dry_run:
                    cli_jar = CLI_DIR / "morphe-cli-mock.jar"
                    patch_file = PATCHES_DIR / "morphe-patches-mock.mpp"
                    cli_tag = "mock"
                    patch_tag = "mock"
                else:
                    group_end()
                    for arch in app.arch:
                        failed_downloads.append(BuildResult(
                            app_name=app.app_name,
                            id=app.id,
                            version="unknown",
                            arch=arch,
                            success=False,
                            error_message=f"Failed to fetch CLI/patches ({app.cli_source} / {app.patches_source})"
                        ))
                    continue

            for arch in app.arch:
                target_info, err = download_single_target(
                    app=app,
                    arch=arch,
                    cli_jar=cli_jar,
                    patch_file=patch_file,
                    cli_tag=cli_tag,
                    patch_tag=patch_tag,
                    dry_run=args.dry_run
                )
                if target_info:
                    download_targets.append(target_info)
                else:
                    failed_downloads.append(BuildResult(
                        app_name=app.app_name,
                        id=app.id,
                        version="unknown",
                        arch=arch,
                        success=False,
                        error_message=err or "Download failed"
                    ))

            group_end()

        save_manifest(download_targets)
        targets_to_patch = download_targets

        if args.download_only:
            log_success(f"Download phase complete. Ready to patch {len(download_targets)} target(s).")
            if failed_downloads:
                for f in failed_downloads:
                    log_warn(f"Failed download: {f.app_name} [{f.arch}] ({f.error_message})")
            return 0 if download_targets else 1

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

    for index, t in enumerate(targets_to_patch, 1):
        app = apps_map.get(t.get("app", t.get("app_name")))
        if not app:
            continue

        group_start(f"Patch: {app.name} [{t['arch']}] ({index}/{len(targets_to_patch)})")
        res = patch_single_target(
            target_info=t,
            app=app,
            general=general,
            dry_run=args.dry_run
        )
        results.append(res)
        group_end()

    return write_build_summary(results)


if __name__ == "__main__":
    sys.exit(main())
