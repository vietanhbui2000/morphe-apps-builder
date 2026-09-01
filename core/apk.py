#!/usr/bin/env python3
"""
APK manipulation utilities: APKEditor bundle merger, architecture stripping, and apksigner.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

from core.github import github_client
from core.http import http_client
from core.logger import log_info, log_error

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
APK_EDITOR = BIN_DIR / "APKEditor.jar"
APK_SIGNER = BIN_DIR / "apksigner.jar"

ARCH_ALIAS_MAP = {
    "arm64": "arm64-v8a",
    "arm64-v8a": "arm64-v8a",
    "arm": "armeabi-v7a",
    "armeabi": "armeabi-v7a",
    "armeabi-v7a": "armeabi-v7a",
    "x86": "x86",
    "x86_64": "x86_64",
}

def ensure_keystore(
    keystore_path: Path,
    keystore_alias: str = "vietanhbui2000",
    keystore_password: str = "1234567890"
) -> bool:
    """Ensure a valid keystore exists; generate a default one using keytool if missing."""
    if keystore_path.is_file() and keystore_path.stat().st_size > 0:
        return True

    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    log_info(f"Keystore not found at {keystore_path}. Generating new keystore with keytool...", indent=2)

    cmd = [
        "keytool", "-genkeypair", "-v",
        "-keystore", str(keystore_path),
        "-storetype", "PKCS12",
        "-alias", keystore_alias,
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-storepass", keystore_password,
        "-keypass", keystore_password,
        "-dname", f"CN={keystore_alias}, O=Morphe"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and keystore_path.is_file() and keystore_path.stat().st_size > 0:
            return True
        log_error(f"Failed to generate keystore via keytool: {res.stderr or res.stdout}", indent=2)
    except Exception as e:
        log_error(f"keytool not available to generate keystore: {e}", indent=2)

    return False

def _resolve_apk_editor_asset() -> Optional[dict]:
    """Resolve the latest APKEditor JAR asset from GitHub."""
    release = github_client.get_release("REAndroid/APKEditor", "latest")
    if release:
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if name.startswith("APKEditor") and name.endswith(".jar"):
                return asset
    return None

def ensure_apk_editor() -> bool:
    if APK_EDITOR.is_file() and APK_EDITOR.stat().st_size > 0:
        return True
    APK_EDITOR.parent.mkdir(parents=True, exist_ok=True)
    asset = _resolve_apk_editor_asset()
    if not asset:
        log_error("Could not resolve latest APKEditor release from GitHub", indent=2)
        return False
    asset_name = asset.get("name", "APKEditor.jar")
    display_name = f"REAndroid_{asset_name}"
    tmp_path = APK_EDITOR.parent / asset_name
    log_info(f"Downloading {display_name}...", indent=2)
    if not http_client.download_file(asset.get("browser_download_url", ""), tmp_path):
        return False
    if tmp_path != APK_EDITOR:
        shutil.move(str(tmp_path), str(APK_EDITOR))
    return True

def merge_bundle(
    bundle_path: Path,
    output_path: Path,
    keystore_path: Optional[Path] = None,
    keystore_alias: str = "",
    keystore_password: str = ""
) -> bool:
    """Merge a split APK bundle (.apkm, .xapk, or split zip) into a standalone APK."""
    if not ensure_apk_editor():
        log_error("APKEditor.jar is not available", indent=2)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_unsigned_path = output_path.parent / f"{output_path.name}.unsigned.apk"

    cmd = [
        "java", "-jar", str(APK_EDITOR),
        "merge",
        "-i", str(bundle_path),
        "-o", str(temp_unsigned_path),
        "-clean-meta",
        "-f"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            log_error(f"APKEditor merge failed: {res.stderr or res.stdout}", indent=2)
            return False

        if temp_unsigned_path.is_file() and temp_unsigned_path.stat().st_size > 0:
            if keystore_path and keystore_path.is_file():
                if sign_apk(
                    apk_path=temp_unsigned_path,
                    keystore_path=keystore_path,
                    keystore_alias=keystore_alias,
                    keystore_password=keystore_password,
                    output_path=output_path
                ):
                    return True
            if output_path.is_file():
                output_path.unlink()
            shutil.move(str(temp_unsigned_path), str(output_path))
            return True
    except Exception as e:
        log_error(f"Error running APKEditor: {e}", indent=2)
    finally:
        if temp_unsigned_path.is_file():
            temp_unsigned_path.unlink(missing_ok=True)

    return False

def get_apk_architectures(apk_path: Path) -> list[str]:
    """Inspect native architectures contained within an APK."""
    if not apk_path.is_file():
        return []

    abis = set()

    try:
        with zipfile.ZipFile(apk_path, "r") as z:
            for name in z.namelist():
                if name.startswith("lib/"):
                    parts = name.split("/")
                    if len(parts) >= 2 and parts[1]:
                        norm = ARCH_ALIAS_MAP.get(parts[1], parts[1])
                        abis.add(norm)
                elif name.endswith(".apk"):
                    name_lower = name.lower()
                    if "arm64" in name_lower or "arm64_v8a" in name_lower:
                        abis.add("arm64-v8a")
                    if "armeabi_v7a" in name_lower or "armeabi-v7a" in name_lower or "arm-v7a" in name_lower or "arm_v7a" in name_lower:
                        abis.add("armeabi-v7a")
                    if "x86_64" in name_lower:
                        abis.add("x86_64")
                    elif "x86" in name_lower:
                        abis.add("x86")
    except Exception as e:
        log_error(f"Failed to inspect APK architectures in {apk_path.name}: {e}", indent=2)

    order = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]
    return sorted(list(abis), key=lambda x: order.index(x) if x in order else 99)

def strip_architectures(apk_path: Path, keep_arch: str, output_path: Path) -> bool:
    """
    Remove all native libraries except those matching keep_arch by streaming zip entries.
    """
    if keep_arch in ("all", "universal", "", "arm64-v8a+armeabi-v7a", "arm64-v8a + armeabi-v7a"):
        if apk_path != output_path:
            shutil.copy2(apk_path, output_path)
        return True
    normalized_keep = ARCH_ALIAS_MAP.get(keep_arch, keep_arch)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out_path = output_path.parent / f"{output_path.name}.stripped.tmp"

    try:
        with zipfile.ZipFile(apk_path, "r") as zin, zipfile.ZipFile(tmp_out_path, "w") as zout:
            for item in zin.infolist():
                if item.filename.startswith("lib/"):
                    parts = item.filename.split("/", 2)
                    if len(parts) >= 2 and parts[1]:
                        abi = ARCH_ALIAS_MAP.get(parts[1], parts[1])
                        if abi != normalized_keep:
                            continue
                zout.writestr(item, zin.read(item.filename))

        if tmp_out_path.is_file() and tmp_out_path.stat().st_size > 0:
            if output_path.is_file():
                output_path.unlink()
            shutil.move(str(tmp_out_path), str(output_path))
            return True
    except Exception as e:
        log_error(f"Failed to strip architectures for {keep_arch}: {e}", indent=2)
        if tmp_out_path.is_file():
            tmp_out_path.unlink(missing_ok=True)

    return False

def sign_apk(
    apk_path: Path,
    keystore_path: Path,
    keystore_alias: str,
    keystore_password: str,
    output_path: Optional[Path] = None
) -> bool:
    """Sign an APK using apksigner with PKCS12 keystore."""
    if not APK_SIGNER.is_file():
        log_error(f"apksigner.jar not found at {APK_SIGNER}", indent=2)
        return False

    if not ensure_keystore(
        keystore_path=keystore_path,
        keystore_alias=keystore_alias,
        keystore_password=keystore_password
    ):
        log_error(f"Keystore file not available at {keystore_path}", indent=2)
        return False

    output_apk_path = output_path or apk_path

    cmd = [
        "java", "-jar", str(APK_SIGNER),
        "sign",
        "--ks", str(keystore_path),
        "--ks-type", "PKCS12",
        "--ks-pass", f"pass:{keystore_password}",
        "--key-pass", f"pass:{keystore_password}",
        "--ks-key-alias", keystore_alias,
        "--out", str(output_apk_path),
        str(apk_path)
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and output_apk_path.is_file() and output_apk_path.stat().st_size > 0:
            return True
        log_error(f"apksigner signing failed: {res.stderr or res.stdout}", indent=2)
    except Exception as e:
        log_error(f"Error executing apksigner: {e}", indent=2)

    return False
