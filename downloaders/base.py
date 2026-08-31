#!/usr/bin/env python3
"""
BaseDownloader abstract interface.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

class BaseDownloader(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. apkmirror, uptodown, etc.)"""
        pass

    @abstractmethod
    def get_versions(self, url: str) -> list[str]:
        """Fetch list of available versions for the given app URL/category."""
        pass

    @abstractmethod
    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path
    ) -> Optional[Path]:
        """
        Download the APK or APK bundle for the given version and target specs.
        Returns the path to the downloaded file (.apk, .apkm, .xapk) if successful, None otherwise.
        """
        pass
