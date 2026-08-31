#!/usr/bin/env python3
"""
Data models for configuration, build targets, and build results.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

@dataclass
class GeneralConfig:
    keystore: str = "keystore.keystore"
    keystore_alias: str = "vietanhbui2000"
    keystore_password: str = "1234567890"
    default_cli_source: str = "MorpheApp/morphe-cli"
    default_patches_source: str = "MorpheApp/morphe-patches"

@dataclass
class AppConfig:
    name: str
    id: str
    app_name: str
    enabled: bool = True
    version: str = "auto"
    arch: list[str] = field(default_factory=lambda: ["all"])
    dpi: str = ""
    cli_source: str = ""
    patches_source: str = ""
    cli_version: str = "latest"
    patches_version: str = "latest"
    apkmirror_url: Optional[str] = None
    uptodown_url: Optional[str] = None
    apkpure_url: Optional[str] = None
    ia_url: Optional[str] = None
    direct_url: Optional[str] = None
    aurora: bool = False
    aurora_url: Optional[str] = None
    included_patches: list[str] = field(default_factory=list)
    excluded_patches: list[str] = field(default_factory=list)
    exclusive_patches: bool = False
    patcher_args: str = ""
    options: dict[str, dict[str, Any]] = field(default_factory=dict)

@dataclass
class BuildTarget:
    app: AppConfig
    target_arch: str

@dataclass
class BuildResult:
    app_name: str
    id: str
    version: str
    arch: str
    success: bool
    output_apk: Optional[Path] = None
    error_message: Optional[str] = None
    cli_source: str = ""
    cli_tag: str = ""
    patches_source: str = ""
    patches_tag: str = ""
