#!/usr/bin/env python3
"""
APK manipulation utilities: APKEditor bundle merger, architecture stripping, and apksigner.
"""

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from core.http import http_client
from core.logger import log_info, log_warn, log_error

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
APK_EDITOR_JAR = BIN_DIR / "APKEditor.jar"
APK_SIGNER_JAR = BIN_DIR / "apksigner.jar"
APK_EDITOR_URL = "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar"

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
        "keytool", "-genkey", "-v",
        "-keystore", str(keystore_path),
        "-alias", keystore_alias,
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-storepass", keystore_password,
        "-keypass", keystore_password,
        "-dname", "CN=Morphe, OU=Builder, O=Morphe, L=Unknown, ST=Unknown, C=US"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and keystore_path.is_file() and keystore_path.stat().st_size > 0:
            return True
        log_error(f"Failed to generate keystore via keytool: {res.stderr or res.stdout}", indent=2)
    except Exception as e:
        log_error(f"keytool not available to generate keystore: {e}", indent=2)

    return False

def ensure_apk_editor() -> bool:
    if APK_EDITOR_JAR.is_file() and APK_EDITOR_JAR.stat().st_size > 0:
        return True
    APK_EDITOR_JAR.parent.mkdir(parents=True, exist_ok=True)
    log_info("Downloading APKEditor.jar...", indent=2)
    return http_client.download_file(APK_EDITOR_URL, APK_EDITOR_JAR)

def merge_bundle(
    bundle_path: Path,
    output_path: Path,
    keystore_path: Optional[Path] = None,
    keystore_password: str = "",
    keystore_alias: str = ""
) -> bool:
    """Merge a split APK bundle (.apkm, .xapk, or split zip) into a standalone APK."""
    if not ensure_apk_editor():
        log_error("APKEditor.jar is not available", indent=2)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_unsigned = output_path.parent / f"{output_path.name}.unsigned.apk"

    cmd = [
        "java", "-jar", str(APK_EDITOR_JAR),
        "merge",
        "-i", str(bundle_path),
        "-o", str(temp_unsigned),
        "-clean-meta",
        "-f"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            log_error(f"APKEditor merge failed: {res.stderr or res.stdout}", indent=2)
            return False

        if temp_unsigned.is_file() and temp_unsigned.stat().st_size > 0:
            if keystore_path and keystore_path.is_file():
                if sign_apk(temp_unsigned, keystore_path, keystore_password, keystore_alias, output_path):
                    return True
            if output_path.is_file():
                output_path.unlink()
            temp_unsigned.rename(output_path)
            return True
    except Exception as e:
        log_error(f"Error running APKEditor: {e}", indent=2)
    finally:
        if temp_unsigned.is_file():
            temp_unsigned.unlink(missing_ok=True)

    return False

def get_apk_architectures(apk_path: Path) -> list[str]:
    """Inspect lib/ directory of an APK or bundle to return list of native ABIs present."""
    if not apk_path.is_file():
        return []
    try:
        with zipfile.ZipFile(apk_path, "r") as z:
            archs = set()
            for name in z.namelist():
                if name.startswith("lib/") and "/" in name[4:]:
                    abi = name[4:].split("/", 1)[0].strip()
                    if abi:
                        archs.add(abi)
            return sorted(list(archs))
    except Exception:
        return []

def strip_archs(apk_path: Path, keep_arch: str, output_path: Path) -> bool:
    """Strip unused native architectures from lib/ inside the APK."""
    if keep_arch in ("all", "universal", ""):
        if apk_path != output_path:
            shutil.copy2(apk_path, output_path)
        return True

    alias_map = {
        "arm-v7a": "armeabi-v7a",
        "armeabi-v7a": "armeabi-v7a",
        "arm64-v8a": "arm64-v8a",
        "x86": "x86",
        "x86_64": "x86_64",
    }
    normalized_keep = alias_map.get(keep_arch, keep_arch)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="apk_strip_"))

    try:
        with zipfile.ZipFile(apk_path, "r") as zin:
            zin.extractall(temp_dir)

        lib_dir = temp_dir / "lib"
        if lib_dir.is_dir():
            for d in lib_dir.iterdir():
                if d.is_dir():
                    norm_name = alias_map.get(d.name, d.name)
                    if norm_name != normalized_keep:
                        shutil.rmtree(d, ignore_errors=True)

        tmp_out = output_path.parent / f"{output_path.name}.stripped.tmp"
        with zipfile.ZipFile(tmp_out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(temp_dir)
                    zout.write(full_path, str(rel_path))

        if tmp_out.is_file() and tmp_out.stat().st_size > 0:
            if output_path.is_file():
                output_path.unlink()
            tmp_out.rename(output_path)
            return True
    except Exception as e:
        log_error(f"Failed to strip architectures for {keep_arch}: {e}", indent=2)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return False

def sign_apk(
    apk_path: Path,
    keystore_path: Path,
    keystore_password: str,
    keystore_alias: str,
    output_path: Optional[Path] = None
) -> bool:
    """Sign an APK using apksigner with automatic keystore type fallback."""
    if not APK_SIGNER_JAR.is_file():
        log_error(f"apksigner.jar not found at {APK_SIGNER_JAR}", indent=2)
        return False

    if not ensure_keystore(keystore_path, keystore_alias, keystore_password):
        log_error(f"Keystore file not available at {keystore_path}", indent=2)
        return False

    out_apk = output_path or apk_path

    # Try default, PKCS12, then JKS to handle all keystore encodings across JDK versions
    for ks_type in (None, "PKCS12", "JKS"):
        cmd = [
            "java", "-jar", str(APK_SIGNER_JAR),
            "sign",
            "--ks", str(keystore_path),
            "--ks-pass", f"pass:{keystore_password}",
            "--key-pass", f"pass:{keystore_password}",
            "--ks-key-alias", keystore_alias,
        ]
        if ks_type:
            cmd.extend(["--ks-type", ks_type])
        cmd.extend(["--out", str(out_apk), str(apk_path)])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and out_apk.is_file() and out_apk.stat().st_size > 0:
                return True
            if ks_type == "JKS":
                log_error(f"apksigner signing failed: {res.stderr or res.stdout}", indent=2)
        except Exception as e:
            if ks_type == "JKS":
                log_error(f"Error executing apksigner: {e}", indent=2)

    return False
