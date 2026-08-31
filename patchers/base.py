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
        app_id: str
    ) -> Optional[str]:
        """Query patch bundle to find the latest supported version for a package."""
        pass

    @abstractmethod
    def patch(
        self,
        cli_path: Path,
        patches_paths: list[Path],
        stock_apk_path: Path,
        output_path: Path,
        app_config: AppConfig,
        keystore_path: Path,
        keystore_alias: str,
        keystore_password: str
    ) -> bool:
        """Run patching process and write final APK."""
        pass
