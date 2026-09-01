#!/usr/bin/env python3
"""
Configuration loader and validator for config.toml using native tomllib.
"""

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

def _parse_patches_list(raw: Any) -> list[str]:
    """Parse patch lists into a clean list of strings."""
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []

def _normalize_arch(raw: Any) -> list[str]:
    """Normalize arch option to a list of target architectures."""
    if isinstance(raw, list):
        archs = [str(a).strip() for a in raw if str(a).strip()]
        if "all" in archs:
            return ["all"]
        return archs or ["universal"]
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        return ["all"] if s == "all" else [s]
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
        keystore_password=gen_data.get("keystore_password", "1234567890"),
        default_cli_source=gen_data.get("default_cli_source", "MorpheApp/morphe-cli"),
        default_cli_version=gen_data.get("default_cli_version", "latest"),
        default_patches_source=gen_data.get("default_patches_source", "MorpheApp/morphe-patches"),
        default_patches_version=gen_data.get("default_patches_version", "latest"),
    )

    apps: list[AppConfig] = []
    for section_name, section_data in data.items():
        if section_name == "general" or not isinstance(section_data, dict):
            continue

        while isinstance(section_data, dict) and "id" not in section_data and len(section_data) == 1:
            nested_k, nested_v = next(iter(section_data.items()))
            if isinstance(nested_v, dict):
                section_name = f"{section_name}.{nested_k}"
                section_data = nested_v
            else:
                break

        enabled = section_data.get("enabled", True)
        app_id = section_data.get("id", "")
        version = section_data.get("version", "auto")
        arch = _normalize_arch(section_data.get("arch", "universal"))
        dpi = section_data.get("dpi", "")

        # URLs & Sources
        aurorastore = section_data.get("aurorastore", False)
        aurorastore_url = section_data.get("aurorastore_url")
        apkmirror_url = section_data.get("apkmirror_url")
        uptodown_url = section_data.get("uptodown_url")
        apkpure_url = section_data.get("apkpure_url")
        ia_url = section_data.get("ia_url")
        direct_url = section_data.get("direct_url")

        # Patcher Overrides
        cli_source = section_data.get("cli_source", general.default_cli_source)
        cli_version = section_data.get("cli_version", general.default_cli_version)
        patches_source = section_data.get("patches_source", general.default_patches_source)
        patches_version = section_data.get("patches_version", general.default_patches_version)

        exclusive_patches = section_data.get("exclusive_patches", False)
        included_patches = _parse_patches_list(section_data.get("included_patches", []))
        excluded_patches = _parse_patches_list(section_data.get("excluded_patches", []))
        patcher_args = section_data.get("patcher_args", "")

        options = section_data.get("options", {})
        if not isinstance(options, dict):
            options = {}

        app_config = AppConfig(
            name=section_name,
            id=app_id,
            enabled=bool(enabled),
            version=str(version),
            arch=arch,
            dpi=str(dpi),
            aurorastore=bool(aurorastore),
            aurorastore_url=aurorastore_url,
            apkmirror_url=apkmirror_url,
            uptodown_url=uptodown_url,
            apkpure_url=apkpure_url,
            ia_url=ia_url,
            direct_url=direct_url,
            cli_source=cli_source,
            cli_version=cli_version,
            patches_source=patches_source,
            patches_version=patches_version,
            exclusive_patches=bool(exclusive_patches),
            included_patches=included_patches,
            excluded_patches=excluded_patches,
            patcher_args=str(patcher_args),
            options=options,
        )
        apps.append(app_config)

    return general, apps
