#!/usr/bin/env python3
"""
BasePatcher abstract interface.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from core.models import AppConfig

class BasePatcher(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def get_compatible_version(
        self,
        cli_path: Path,
        patches_path: Path,
        app_id: str,
        mode: str = "auto"
    ) -> Optional[str]:
        """Query patch bundle to find compatible version ('auto' for all patches, 'latest' for newest, 'beta' for beta/pre-release)."""
        pass

    @abstractmethod
    def patch(
        self,
        cli_path: Path,
        patches_path: Path,
        stock_apk_path: Path,
        output_path: Path,
        app_config: AppConfig,
        keystore_path: Path,
        keystore_alias: str,
        keystore_password: str
    ) -> bool:
        """Run patching process and write final APK."""
        pass
