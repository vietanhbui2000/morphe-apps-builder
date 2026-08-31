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

    release_md = ROOT_DIR / "release.md"
    prev_release_text = release_md.read_text(encoding="utf-8") if release_md.is_file() else ""
    should_build = not bool(prev_release_text)

    for cli_source, patches_source in sources:
        log_info(f"Checking {cli_source}...")
        cli_rel = github_client.get_release(cli_source, "latest")
        if cli_rel:
            cli_tag = cli_rel.get("tag_name", "")
            log_success(f"Latest CLI: {cli_tag} ({cli_source})", indent=1)
            if cli_tag and cli_tag not in prev_release_text:
                should_build = True

        log_info(f"Checking {patches_source}...")
        patches_rel = github_client.get_release(patches_source, "latest")
        if patches_rel:
            patches_tag = patches_rel.get("tag_name", "")
            log_success(f"Latest Patches: {patches_tag} ({patches_source})", indent=1)
            if patches_tag and patches_tag not in prev_release_text:
                should_build = True

    if should_build:
        print("SHOULD_BUILD=1")
    else:
        print("SHOULD_BUILD=0")

    return 0


def resolve_app_version(
    app: AppConfig,
    cli_path: Path,
    patches_path: Path,
    dry_run: bool = False
) -> Optional[str]:
    """Resolve target version for app (from config, patches compatibility list, or fallback)."""
    if app.version != "auto":
        log_info(f"Using explicitly configured version: {app.version}", indent=1)
        return app.version

    if dry_run:
        return "auto"

    # Try resolving from patch bundle compatibility list
    if cli_path.is_file() and patches_path.is_file():
        resolved = morphe_patcher.get_compatible_version(cli_path, patches_path, app.id)
        if resolved:
            log_success(f"Resolved compatible version from patch bundle: {resolved}", indent=1)
            return resolved

    # Fallback to scraping first available version from download providers
    log_warn("Version not found in patch bundle. Falling back to latest from sources...", indent=1)
    sources = get_download_sources_for_app(app)
    for _, dl_inst, src_url in sources:
        vers = dl_inst.get_versions(src_url)
        if vers:
            log_success(f"Fallback version resolved from {dl_inst.display_name}: {vers[0]}", indent=1)
            return vers[0]

    return "auto" if dry_run else None


def download_single_target(
    app: AppConfig,
    arch: str,
    cli_tag: str,
    cli_path: Path,
    patches_tag: str,
    patches_path: Path,
    dry_run: bool = False
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Download phase for an architecture target or dynamic multi-architecture expansion ('all').
    Returns (list_of_target_info_dicts, error_message).
    """
    log_stage(f"Downloading {app.name} ({arch})")

    resolved_version = resolve_app_version(app, cli_path, patches_path, dry_run=dry_run)
    if not resolved_version:
        return [], "Could not resolve version for app"

    if dry_run:
        log_info(f"[DRY-RUN] Would download {app.name} v{resolved_version} ({arch})", indent=1)
        target_info = {
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
            "stock_apk_path": "",
        }
        if arch == "all":
            return [
                {**target_info, "arch": "universal"},
                {**target_info, "arch": "arm64-v8a"},
                {**target_info, "arch": "armeabi-v7a"},
            ], None
        return [{**target_info, "arch": arch}], None

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
                output_path=stock_apk_base,
                app_id=app.id
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

    target_info = {
        "name": app.name,
        "id": app.id,
        "version": resolved_version,
        "stock_apk_path": str(downloaded_file),
        "cli_source": app.cli_source,
        "cli_version": app.cli_version,
        "cli_tag": cli_tag,
        "cli_path": str(cli_path),
        "patches_source": app.patches_source,
        "patches_version": app.patches_version,
        "patches_tag": patches_tag,
        "patches_path": str(patches_path),
    }

    if arch == "all":
        # Inspect native ABIs inside the downloaded APK / bundle
        detected_abis = get_apk_architectures(downloaded_file)
        if detected_abis:
            targets = [{**target_info, "arch": "universal"}]
            for abi in detected_abis:
                targets.append({**target_info, "arch": abi})
            log_success(f"Detected architectures: universal, {', '.join(detected_abis)}", indent=1)
            return targets, None
        return [{**target_info, "arch": "universal"}], None

    return [{**target_info, "arch": arch}], None


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

    log_stage(f"Patching {name} ({arch}) v{version}")

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

    downloaded_file = Path(target_info.get("stock_apk_path", target_info.get("stock_apk", "")))
    if not downloaded_file.is_file():
        return BuildResult(
            name=name,
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
    stock_apk_path: Path
    stock_apk_base = APKS_DIR / f"{app.id}_{version}_{arch}"
    keystore_path = ROOT_DIR / general.keystore
    ensure_keystore(keystore_path, general.keystore_alias, general.keystore_password)

    if downloaded_file.suffix in (".apkm", ".xapk") or "bundle" in downloaded_file.name:
        merged_apk = stock_apk_base.parent / f"{stock_apk_base.name}.merged.apk"
        log_info(f"Merging split bundle {downloaded_file.name} to standalone APK...", indent=1)
        if not merge_bundle(
            bundle_path=downloaded_file,
            output_path=merged_apk,
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
        stock_apk_path = merged_apk
    else:
        stock_apk_path = downloaded_file

    # 2. Patching
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    arch_suffix = "" if arch in ("all", "universal", "") else f"_{arch}"
    final_apk_name = f"{name}_v{version}{arch_suffix}.apk"
    output_path = OUTPUT_DIR / final_apk_name
    temp_patched = TEMP_DIR / f"patched_{final_apk_name}"

    log_info(f"Applying patches for {name}...", indent=1)
    patch_success = morphe_patcher.patch(
        cli_path=cli_path,
        patches_paths=[patches_path],
        stock_apk_path=stock_apk_path,
        output_path=temp_patched,
        app_config=app,
        keystore_path=keystore_path,
        keystore_alias=general.keystore_alias,
        keystore_password=general.keystore_password,
    )

    if not patch_success or not temp_patched.is_file():
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

    # 3. Native Architecture Stripping & Signing
    if arch not in ("all", "universal", ""):
        log_info(f"Filtering native libraries for {arch}...", indent=1)
        stripped_apk = TEMP_DIR / f"stripped_{final_apk_name}"
        if strip_archs(temp_patched, arch, stripped_apk):
            temp_patched = stripped_apk

    log_info("Signing release APK...", indent=1)
    if not sign_apk(
        apk_path=temp_patched,
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
    """Generate console summary and release.md."""
    print("=" * 70)
    print(f"{Colors.BOLD}BUILD SUMMARY{Colors.RESET}")
    print("=" * 70)

    # Group results by name maintaining order
    grouped: Dict[str, List[BuildResult]] = {}
    for r in results:
        grouped.setdefault(r.name, []).append(r)

    for name, app_results in grouped.items():
        all_success = all(r.success for r in app_results)
        any_success = any(r.success for r in app_results)

        if all_success:
            status = f"[{Colors.GREEN}✓ SUCCESS{Colors.RESET}]"
            apk_names = [
                r.output_path.name if r.output_path else f"{r.name}_v{r.version}{'' if r.arch in ('all', 'universal', '') else f'_{r.arch}'}.apk"
                for r in app_results
            ]
            print(f"{name}: {status} -> {', '.join(apk_names)}")
        elif any_success:
            status = f"[{Colors.YELLOW}⚠ PARTIAL{Colors.RESET}]"
            parts = []
            for r in app_results:
                if r.success and r.output_path:
                    parts.append(r.output_path.name)
                else:
                    parts.append(f"{r.arch} failed ({r.error_message})")
            print(f"{name}: {status} -> {', '.join(parts)}")
        else:
            status = f"[{Colors.RED}✗ FAILED{Colors.RESET}]"
            err_msgs = list(dict.fromkeys(r.error_message for r in app_results if r.error_message))
            err_str = f" ({', '.join(err_msgs)})" if err_msgs else ""
            print(f"{name}: {status}{err_str}")

    # Write release.md for GitHub Releases
    release_md = ROOT_DIR / "release.md"
    sections = []

    repo = _get_github_repo()
    release_tag = os.environ.get("RELEASE_TAG", "").strip()

    app_blocks = []
    for name, app_results in grouped.items():
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
                apk_name = r.output_path.name if r.output_path else f"{r.name}_{r_ver}{'' if r.arch in ('all', 'universal', '') else f'_{r.arch}'}.apk"
                dl_url = f"https://github.com/{repo}/releases/download/{release_tag}/{apk_name}"
                target_links.append(f"[{label}]({dl_url})")
            else:
                target_links.append(label)

        targets_str = " | ".join(target_links)
        header = f"{name}: {targets_str}  "

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

            for arch in app.arch:
                targets_list, err = download_single_target(
                    app=app,
                    arch=arch,
                    cli_tag=cli_tag,
                    cli_path=cli_path,
                    patches_tag=patches_tag,
                    patches_path=patches_path,
                    dry_run=args.dry_run
                )
                if targets_list:
                    download_targets.extend(targets_list)
                else:
                    failed_downloads.append(BuildResult(
                        name=app.name,
                        id=app.id,
                        version="unknown",
                        arch=arch,
                        success=False,
                        error_message=err or "Download failed",
                        cli_source=app.cli_source,
                        cli_tag=cli_tag,
                        patches_source=app.patches_source,
                        patches_tag=patches_tag,
                    ))

            group_end()

        save_manifest(download_targets)
        targets_to_patch = download_targets

        if args.download_only:
            log_success(f"Download phase complete. Ready to patch {len(download_targets)} target(s).")
            if failed_downloads:
                for f in failed_downloads:
                    log_warn(f"Failed download: {f.name} ({f.arch}) ({f.error_message})")
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

    all_results = failed_downloads + results if not args.patch_only else results
    return write_build_summary(all_results)


if __name__ == "__main__":
    sys.exit(main())
