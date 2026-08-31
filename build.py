#!/usr/bin/env python3
"""
Morphe Apps Builder — Main CLI Orchestrator.
Supports unified all-in-one builds as well as decoupled --download-only and --patch-only workflows.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.apk import merge_bundle, strip_archs, sign_apk, ensure_apk_editor, ensure_keystore, get_apk_architectures
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
    """Remove temporary and build output files."""
    log_info("Cleaning temporary and output artifacts...")
    for directory in (TEMP_DIR, OUTPUT_DIR):
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
    release_md = ROOT_DIR / "release.md"
    if release_md.is_file():
        release_md.unlink(missing_ok=True)
    log_success("Workspace cleaned.")


def check_updates(config_path: Path) -> int:
    """Check for upstream CLI and patches updates across all enabled apps."""
    general, apps = load_config(config_path)
    enabled_apps = [a for a in apps if a.enabled]

    log_stage("Checking for upstream updates")
    sources = set((a.cli_source, a.patches_source) for a in enabled_apps)

    for cli_src, patches_src in sources:
        log_info(f"Checking {cli_src}...")
        cli_rel = github_client.get_release(cli_src, "latest")
        if cli_rel:
            log_success(f"Latest CLI: {cli_rel.get('tag_name')} ({cli_src})", indent=1)

        log_info(f"Checking {patches_src}...")
        patches_rel = github_client.get_release(patches_src, "latest")
        if patches_rel:
            log_success(f"Latest Patches: {patches_rel.get('tag_name')} ({patches_src})", indent=1)

    return 0


def resolve_app_version(
    app: AppConfig,
    cli_jar: Path,
    patch_file: Path,
    dry_run: bool = False
) -> Optional[str]:
    """Resolve target version for app (from config, patches compatibility list, or fallback)."""
    if app.version != "auto":
        log_info(f"Using explicitly configured version: {app.version}", indent=1)
        return app.version

    if dry_run:
        return "auto"

    # Try resolving from patch bundle compatibility list
    if cli_jar.is_file() and patch_file.is_file():
        resolved = morphe_patcher.get_compatible_version(cli_jar, patch_file, app.id)
        if resolved:
            log_success(f"Resolved compatible version from patch bundle: {resolved}", indent=1)
            return resolved

    # Fallback to scraping first available version from download providers
    log_warn("Version not found in patch bundle. Falling back to latest from sources...", indent=1)
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
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Download phase for an architecture target or dynamic multi-architecture expansion ('all').
    Returns (list_of_target_info_dicts, error_message).
    """
    print()
    log_stage(f"Downloading {app.app_name} ({arch})")

    resolved_version = resolve_app_version(app, cli_jar, patch_file, dry_run=dry_run)
    if not resolved_version:
        return [], "Could not resolve version for app"

    if dry_run:
        log_info(f"[DRY-RUN] Would download {app.app_name} v{resolved_version} ({arch})", indent=1)
        base_dict = {
            "app": app.name,
            "id": app.id,
            "version": resolved_version,
            "cli_source": app.cli_source,
            "cli_version": cli_tag,
            "cli_file": str(cli_jar),
            "patches_source": app.patches_source,
            "patches_version": patch_tag,
            "patches_file": str(patch_file),
            "stock_apk": "",
        }
        if arch == "all":
            return [
                {**base_dict, "arch": "universal"},
                {**base_dict, "arch": "arm64-v8a"},
                {**base_dict, "arch": "armeabi-v7a"},
            ], None
        return [{**base_dict, "arch": arch}], None

    sources = get_download_sources_for_app(app)
    if not sources:
        return [], "No download sources configured in config.toml"

    APKS_DIR.mkdir(parents=True, exist_ok=True)
    stock_apk_base = APKS_DIR / f"{app.id}_{resolved_version}_{arch}"
    downloaded_file: Optional[Path] = None

    download_arch_query = "universal" if arch == "all" else arch
    for provider_name, dl_inst, src_url in sources:
        log_info(f"Attempting download via {provider_name}...", indent=1)
        try:
            downloaded_file = dl_inst.download(
                url=src_url,
                version=resolved_version,
                arch=download_arch_query,
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
        return [], "All download providers failed"

    base_info = {
        "app": app.name,
        "id": app.id,
        "version": resolved_version,
        "stock_apk": str(downloaded_file),
        "cli_source": app.cli_source,
        "cli_version": cli_tag,
        "cli_file": str(cli_jar),
        "patches_source": app.patches_source,
        "patches_version": patch_tag,
        "patches_file": str(patch_file),
    }

    if arch == "all":
        # Inspect native ABIs inside the downloaded APK / bundle
        detected_abis = get_apk_architectures(downloaded_file)
        if detected_abis:
            targets = [{**base_info, "arch": "universal"}]
            for abi in detected_abis:
                targets.append({**base_info, "arch": abi})
            log_success(f"Detected architectures: universal, {', '.join(detected_abis)}", indent=1)
            return targets, None
        return [{**base_info, "arch": "universal"}], None

    return [{**base_info, "arch": arch}], None


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

    log_stage(f"Patching {app_name} ({arch}) v{version}")

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
    arch_suffix = "" if arch in ("all", "universal", "") else f"_{arch}"
    final_apk_name = f"{app_name}_v{version}{arch_suffix}.apk"
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


def write_build_summary(results: List[BuildResult]) -> int:
    """Generate console summary and build.md."""
    print("\n" + "=" * 70)
    print(f"{Colors.BOLD}BUILD SUMMARY{Colors.RESET}")
    print("=" * 70)

    # Group results by app_name maintaining order
    grouped: Dict[str, List[BuildResult]] = {}
    for r in results:
        grouped.setdefault(r.app_name, []).append(r)

    for app_name, app_results in grouped.items():
        all_success = all(r.success for r in app_results)
        any_success = any(r.success for r in app_results)

        if all_success:
            status = f"[{Colors.GREEN}✓ SUCCESS{Colors.RESET}]"
            apk_names = [
                r.output_apk.name if r.output_apk else f"{r.app_name}_v{r.version}{'' if r.arch in ('all', 'universal', '') else f'_{r.arch}'}.apk"
                for r in app_results
            ]
            print(f"{app_name} {status} -> {', '.join(apk_names)}")
        elif any_success:
            status = f"[{Colors.YELLOW}⚠ PARTIAL{Colors.RESET}]"
            parts = []
            for r in app_results:
                if r.success and r.output_apk:
                    parts.append(r.output_apk.name)
                else:
                    parts.append(f"{r.arch} failed ({r.error_message})")
            print(f"{app_name} {status} -> {', '.join(parts)}")
        else:
            status = f"[{Colors.RED}✗ FAILED{Colors.RESET}]"
            err_msgs = list(dict.fromkeys(r.error_message for r in app_results if r.error_message))
            err_str = f" ({', '.join(err_msgs)})" if err_msgs else ""
            print(f"{app_name} {status}{err_str}")

    # Write release.md for GitHub Releases
    release_md = ROOT_DIR / "release.md"
    sections = []

    repo = _get_github_repo()
    release_tag = os.environ.get("RELEASE_TAG", "").strip()

    app_blocks = []
    for app_name, app_results in grouped.items():
        success_results = [r for r in app_results if r.success]
        if not success_results:
            continue

        first_r = success_results[0]
        target_links = []
        is_multi = len(success_results) > 1
        for r in success_results:
            r_ver = f"v{r.version}" if not r.version.startswith("v") else r.version
            if is_multi or r.arch not in ("all", "universal", ""):
                label = f"{r_ver} ({r.arch})"
            else:
                label = r_ver

            if repo and release_tag:
                apk_name = r.output_apk.name if r.output_apk else f"{r.app_name}_{r_ver}{'' if r.arch in ('all', 'universal', '') else f'_{r.arch}'}.apk"
                dl_url = f"https://github.com/{repo}/releases/download/{release_tag}/{apk_name}"
                target_links.append(f"[{label}]({dl_url})")
            else:
                target_links.append(label)

        targets_str = " | ".join(target_links)
        header = f"{app_name}: {targets_str}  "

        cli_link = f"[{first_r.cli_tag}](https://github.com/{first_r.cli_source}/releases/tag/{first_r.cli_tag})" if first_r.cli_tag else ""
        cli_str = f"{first_r.cli_source} {cli_link}".strip()

        patches_link = f"[{first_r.patches_tag}](https://github.com/{first_r.patches_source}/releases/tag/{first_r.patches_tag})" if first_r.patches_tag else ""
        patches_str = f"{first_r.patches_source} {patches_link}".strip()

        sub = f"└ {cli_str} + {patches_str}"
        app_blocks.append(f"{header}\n{sub}")

    if app_blocks:
        sections.append("\n\n".join(app_blocks))
        sections.append("ℹ Install [MicroG ↗](https://github.com/MorpheApp/MicroG-RE/) to enable Google account authentication and services for Morphe apps.")

    release_md.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    log_success(f"Wrote release notes to {release_md.name}")

    return 0 if any(r.success for r in results) else 1


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
        print()
        log_stage("Starting Download Phase (Prebuilts & Stock APKs)")
        download_targets: List[Dict[str, Any]] = []
        failed_downloads: List[BuildResult] = []

        total_download_tasks = len(enabled_apps) + 1

        # 1. Download Prebuilts (CLI & Patches)
        group_start(f"Download [1/{total_download_tasks}]: Prebuilts (CLI & Patches)")
        log_stage("Fetching Prebuilt Tools, CLI & Patches")

        # bin tools
        if not args.dry_run:
            ensure_apk_editor()
        else:
            log_info("[DRY-RUN] Would download APKEditor.jar...", indent=2)

        prebuilts_cache: Dict[Tuple[str, str, str, str], Tuple[Optional[Path], Optional[Path], str, str]] = {}
        unique_prebuilts = list(dict.fromkeys(
            (app.cli_source, app.cli_version, app.patches_source, app.patches_version)
            for app in enabled_apps
        ))

        for cli_repo, cli_ver, patches_repo, patches_ver in unique_prebuilts:
            cli_jar, patch_file, cli_tag, patch_tag = github_client.get_prebuilts(
                cli_repo=cli_repo,
                cli_tag=cli_ver,
                patches_repo=patches_repo,
                patches_tag=patches_ver,
                cli_dir=CLI_DIR,
                patches_dir=PATCHES_DIR
            )
            if not cli_jar or not patch_file:
                if args.dry_run:
                    cli_jar = CLI_DIR / "morphe-cli-mock.jar"
                    patch_file = PATCHES_DIR / "morphe-patches-mock.mpp"
                    cli_tag = "mock"
                    patch_tag = "mock"
            prebuilts_cache[(cli_repo, cli_ver, patches_repo, patches_ver)] = (
                cli_jar, patch_file, cli_tag, patch_tag
            )
        group_end()

        # 2. Download Stock APKs for each enabled app
        for app_idx, app in enumerate(enabled_apps, 1):
            task_idx = app_idx + 1
            group_start(f"Download [{task_idx}/{total_download_tasks}]: {app.name}")
            log_app_banner(app_idx, len(enabled_apps), app.app_name, app.id)

            cli_jar, patch_file, cli_tag, patch_tag = prebuilts_cache.get(
                (app.cli_source, app.cli_version, app.patches_source, app.patches_version),
                (None, None, "", "")
            )

            if not cli_jar or not patch_file:
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
                targets_list, err = download_single_target(
                    app=app,
                    arch=arch,
                    cli_jar=cli_jar,
                    patch_file=patch_file,
                    cli_tag=cli_tag,
                    patch_tag=patch_tag,
                    dry_run=args.dry_run
                )
                if targets_list:
                    download_targets.extend(targets_list)
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
                    log_warn(f"Failed download: {f.app_name} ({f.arch}) ({f.error_message})")
            return 0 if download_targets else 1

    # --------------------------------------------------------------------------
    # PHASE 2: PATCH & BUILD PHASE
    # --------------------------------------------------------------------------
    if args.patch_only:
        targets_to_patch = load_manifest()
        if not targets_to_patch:
            log_error("No downloaded targets found in manifest. Run with --download-only first or without flags.")
            return 1

    print()
    log_stage("Starting Patching & Signing Phase")
    results: List[BuildResult] = []

    for index, t in enumerate(targets_to_patch, 1):
        app = apps_map.get(t.get("app", t.get("app_name")))
        if not app:
            continue

        group_start(f"Patch [{index}/{len(targets_to_patch)}]: {app.name} ({t['arch']})")
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
