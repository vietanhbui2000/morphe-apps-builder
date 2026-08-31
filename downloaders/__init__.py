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
from downloaders.aurora import AuroraDownloader

DOWNLOADERS = {
    "apkmirror": APKMirrorDownloader(),
    "uptodown": UptodownDownloader(),
    "apkpure": APKPureDownloader(),
    "ia": IADownloader(),
    "direct": DirectDownloader(),
    "aurora": AuroraDownloader(),
}

def get_download_sources_for_app(app: AppConfig) -> List[Tuple[str, BaseDownloader, str]]:
    """Return ordered list of (provider_name, downloader_instance, source_url) configured for this app."""
    sources: List[Tuple[str, BaseDownloader, str]] = []

    if app.aurora or app.aurora_url:
        sources.append(("AuroraStore", DOWNLOADERS["aurora"], app.aurora_url or "https://auroraoss.com/api/auth"))
    if app.apkmirror_url:
        sources.append(("APKMirror", DOWNLOADERS["apkmirror"], app.apkmirror_url))
    if app.uptodown_url:
        sources.append(("Uptodown", DOWNLOADERS["uptodown"], app.uptodown_url))
    if app.apkpure_url:
        sources.append(("APKPure", DOWNLOADERS["apkpure"], app.apkpure_url))
    if app.ia_url:
        sources.append(("Internet Archive", DOWNLOADERS["ia"], app.ia_url))
    if app.direct_url:
        sources.append(("Direct", DOWNLOADERS["direct"], app.direct_url))

    return sources
