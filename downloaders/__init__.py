#!/usr/bin/env python3
"""
Registry and dispatcher for modular downloaders.
"""

from typing import List, Tuple
from core.models import AppConfig
from downloaders.base import BaseDownloader
from downloaders.apkmirror import APKMirrorDownloader
from downloaders.uptodown import UptodownDownloader
from downloaders.apkpure import APKPureDownloader
from downloaders.ia import IADownloader
from downloaders.direct import DirectDownloader
from downloaders.aurorastore import AuroraStoreDownloader

DOWNLOADERS = {
    "apkmirror": APKMirrorDownloader(),
    "uptodown": UptodownDownloader(),
    "apkpure": APKPureDownloader(),
    "ia": IADownloader(),
    "direct": DirectDownloader(),
    "aurorastore": AuroraStoreDownloader(),
}

def get_download_sources_for_app(app: AppConfig) -> List[Tuple[str, BaseDownloader, str]]:
    """Return ordered list of (display_name, downloader_instance, source_url) configured for this app."""
    sources: List[Tuple[str, BaseDownloader, str]] = []

    if app.aurorastore or app.aurorastore_url:
        dl = DOWNLOADERS["aurorastore"]
        sources.append((dl.display_name, dl, app.aurorastore_url or "https://auroraoss.com/api/auth"))
    if app.apkmirror_url:
        dl = DOWNLOADERS["apkmirror"]
        sources.append((dl.display_name, dl, app.apkmirror_url))
    if app.uptodown_url:
        dl = DOWNLOADERS["uptodown"]
        sources.append((dl.display_name, dl, app.uptodown_url))
    if app.apkpure_url:
        dl = DOWNLOADERS["apkpure"]
        sources.append((dl.display_name, dl, app.apkpure_url))
    if app.ia_url:
        dl = DOWNLOADERS["ia"]
        sources.append((dl.display_name, dl, app.ia_url))
    if app.direct_url:
        dl = DOWNLOADERS["direct"]
        sources.append((dl.display_name, dl, app.direct_url))

    return sources
