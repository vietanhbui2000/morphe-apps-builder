#!/usr/bin/env python3
"""
Configuration loader and validator for config.toml using native tomllib.
"""

import re
import sys
from pathlib import Path
from typing import Any, Tuple

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("Error: tomllib / tomli not found. Python 3.11+ is recommended, or run 'pip install tomli'.", file=sys.stderr)
        sys.exit(1)

from core.models import AppConfig, GeneralConfig

def _parse_patch_list(raw: Any) -> list[str]:
    """Parse patch lists that may be a list of strings or a single string containing quoted patches."""
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    if isinstance(raw, str):
        # Match single-quoted or double-quoted items or words
        matches = re.findall(r"'([^']*)'|\"([^\"]*)\"|(\S+)", raw)
        patches = []
        for m in matches:
            val = m[0] or m[1] or m[2]
            if val.strip():
                patches.append(val.strip())
        return patches
    return []

def _normalize_arch(raw: Any) -> list[str]:
    """Normalize arch option to a list of target architectures."""
    if isinstance(raw, list):
        archs = [str(a).strip() for a in raw if str(a).strip()]
        if "all" in archs:
            return ["all"]
        return archs or ["universal"]
    if isinstance(raw, str):
        s = raw.strip()
        if s == "all":
            return ["all"]
        if s == "both":
            return ["arm64-v8a", "armeabi-v7a"]
        return [s] if s else ["universal"]
    return ["universal"]

def load_config(config_path: Path) -> Tuple[GeneralConfig, list[AppConfig]]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # General configuration
    gen_data = data.get("general", {})
    general = GeneralConfig(
        keystore=gen_data.get("keystore", "keystore.keystore"),
        keystore_alias=gen_data.get("keystore_alias", "vietanhbui2000"),
        keystore_password=gen_data.get("keystore_password", gen_data.get("keystore_pass", "1234567890")),
        default_cli_source=gen_data.get("default_cli_source", "MorpheApp/morphe-cli"),
        default_patches_source=gen_data.get("default_patches_source", "MorpheApp/morphe-patches"),
    )

    apps: list[AppConfig] = []
    for section_name, section_data in data.items():
        if section_name == "general" or not isinstance(section_data, dict):
            continue

        curr_name = section_name
        curr_data = section_data
        while isinstance(curr_data, dict) and "id" not in curr_data and "pkg" not in curr_data and len(curr_data) == 1:
            nested_k, nested_v = next(iter(curr_data.items()))
            if isinstance(nested_v, dict):
                curr_name = f"{curr_name}.{nested_k}"
                curr_data = nested_v
            else:
                break

        section_name = curr_name
        section_data = curr_data

        enabled = section_data.get("enabled", True)
        app_id = section_data.get("id") or section_data.get("pkg") or section_data.get("pkg_name", "")
        app_name = section_data.get("app_name", section_name)
        version = section_data.get("version", "auto")
        arch = _normalize_arch(section_data.get("arch", "all"))
        dpi = section_data.get("dpi", "")

        cli_source = section_data.get("cli_source", general.default_cli_source)
        patches_source = section_data.get("patches_source", general.default_patches_source)
        cli_version = section_data.get("cli_version", "latest")
        patches_version = section_data.get("patches_version", "latest")

        # URLs & Sources
        apkmirror_url = section_data.get("apkmirror_url")
        uptodown_url = section_data.get("uptodown_url")
        apkpure_url = section_data.get("apkpure_url")
        ia_url = section_data.get("ia_url")
        direct_url = section_data.get("direct_url")
        aurora = section_data.get("aurora", False)
        aurora_url = section_data.get("aurora_url")

        included_patches = _parse_patch_list(section_data.get("included_patches") or section_data.get("included-patches", []))
        excluded_patches = _parse_patch_list(section_data.get("excluded_patches") or section_data.get("excluded-patches", []))
        exclusive_patches = section_data.get("exclusive_patches", section_data.get("exclusive-patches", False))
        patcher_args = section_data.get("patcher_args", section_data.get("patcher-args", ""))

        options = section_data.get("options", {})
        if not isinstance(options, dict):
            options = {}

        app_config = AppConfig(
            name=section_name,
            id=app_id,
            app_name=app_name,
            enabled=bool(enabled),
            version=str(version),
            arch=arch,
            dpi=str(dpi),
            cli_source=cli_source,
            patches_source=patches_source,
            cli_version=cli_version,
            patches_version=patches_version,
            apkmirror_url=apkmirror_url,
            uptodown_url=uptodown_url,
            apkpure_url=apkpure_url,
            ia_url=ia_url,
            direct_url=direct_url,
            aurora=bool(aurora),
            aurora_url=aurora_url,
            included_patches=included_patches,
            excluded_patches=excluded_patches,
            exclusive_patches=bool(exclusive_patches),
            patcher_args=str(patcher_args),
            options=options,
        )
        apps.append(app_config)

    return general, apps
