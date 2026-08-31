#!/usr/bin/env python3
"""
Aurora Store / Google Play Downloader: connects to AuroraStore dispenser,
authenticates with Google Play API, and downloads base or split APKs.
"""

import base64
import hashlib
import json
import os
import random
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from core.logger import log_info, log_warn, log_error
from downloaders.base import BaseDownloader

try:
    from downloaders.aurora_pb2 import (
        AndroidCheckinRequest,
        AndroidCheckinProto,
        AndroidBuildProto,
        AndroidCheckinResponse,
        DeviceConfigurationProto,
        UploadDeviceConfigRequest,
        ResponseWrapper,
    )
    HAS_PROTOBUF = True
except ImportError:
    HAS_PROTOBUF = False

DEFAULT_DISPENSER_URL = "https://auroraoss.com/api/auth"
URL_BASE = "https://android.clients.google.com"
URL_FDFE = f"{URL_BASE}/fdfe"
URL_CHECK_IN = f"{URL_BASE}/checkin"
URL_AUTH = f"{URL_BASE}/auth"
URL_UPLOAD_DEVICE_CONFIG = f"{URL_FDFE}/uploadDeviceConfig"
URL_DETAILS = f"{URL_FDFE}/details"
URL_PURCHASE = f"{URL_FDFE}/purchase"
URL_DELIVERY = f"{URL_FDFE}/delivery"
URL_ACQUIRE = f"{URL_FDFE}/acquire"

DEVICE_PROPERTIES = {
    "UserReadableName": "Google Pixel 9a",
    "Build.DEVICE": "tegu",
    "Build.MODEL": "Pixel 9a",
    "Build.MANUFACTURER": "Google",
    "Build.BRAND": "google",
    "Build.PRODUCT": "tegu",
    "Build.HARDWARE": "tegu",
    "Build.FINGERPRINT": "google/tegu/tegu:15/BD4A.250405.003/13238919:user/release-keys",
    "Build.VERSION.SDK_INT": "35",
    "Build.VERSION.RELEASE": "15",
    "Build.ID": "BD4A.250405.003",
    "Build.BOOTLOADER": "tegu-16.0-13238451",
    "Build.RADIO": "g5300t-241101-241226-B-12850354",
    "GSF.version": "251333035",
    "Vending.version": "84582130",
    "Vending.versionString": "45.8.21-31 [0] [PR] 747433787",
    "Client": "android-google",
    "SimOperator": "38",
    "CellOperator": "310",
    "Screen.Density": "420",
    "Screen.Width": "1080",
    "Screen.Height": "2424",
    "Platforms": "arm64-v8a",
    "Features": "android.hardware.bluetooth,android.hardware.camera,android.hardware.location,android.hardware.microphone,android.hardware.nfc,android.hardware.screen.landscape,android.hardware.screen.portrait,android.hardware.telephony,android.hardware.touchscreen,android.hardware.wifi",
    "Locales": "en_US",
    "SharedLibraries": "",
    "TouchScreen": "3",
    "Keyboard": "1",
    "Navigation": "1",
    "ScreenLayout": "2",
    "HasHardKeyboard": "false",
    "HasFiveWayNavigation": "false",
    "GL.Version": "196610",
    "GL.Extensions": "",
    "TimeZone": "UTC-0",
    "Roaming": "mobile-notroaming",
}


def _build_device_config():
    config = DeviceConfigurationProto()
    config.touchScreen = int(DEVICE_PROPERTIES["TouchScreen"])
    config.keyboard = int(DEVICE_PROPERTIES["Keyboard"])
    config.navigation = int(DEVICE_PROPERTIES["Navigation"])
    config.screenLayout = int(DEVICE_PROPERTIES["ScreenLayout"])
    config.hasHardKeyboard = DEVICE_PROPERTIES["HasHardKeyboard"] == "true"
    config.hasFiveWayNavigation = DEVICE_PROPERTIES["HasFiveWayNavigation"] == "true"
    config.screenDensity = int(DEVICE_PROPERTIES["Screen.Density"])
    config.glEsVersion = int(DEVICE_PROPERTIES["GL.Version"])
    config.screenWidth = int(DEVICE_PROPERTIES["Screen.Width"])
    config.screenHeight = int(DEVICE_PROPERTIES["Screen.Height"])

    for platform in DEVICE_PROPERTIES["Platforms"].split(","):
        config.nativePlatform.append(platform.strip())

    for feature in DEVICE_PROPERTIES["Features"].split(","):
        if feature.strip():
            config.systemAvailableFeature.append(feature.strip())

    for locale in DEVICE_PROPERTIES["Locales"].split(","):
        if locale.strip():
            config.systemSupportedLocale.append(locale.strip())

    for lib in DEVICE_PROPERTIES["SharedLibraries"].split(","):
        if lib.strip():
            config.systemSharedLibrary.append(lib.strip())

    for ext in DEVICE_PROPERTIES["GL.Extensions"].split(","):
        if ext.strip():
            config.glExtension.append(ext.strip())

    return config


def _build_checkin_request():
    build = AndroidBuildProto()
    build.device = DEVICE_PROPERTIES["Build.DEVICE"]
    build.model = DEVICE_PROPERTIES["Build.MODEL"]
    build.manufacturer = DEVICE_PROPERTIES["Build.MANUFACTURER"]
    build.product = DEVICE_PROPERTIES["Build.PRODUCT"]
    build.buildProduct = DEVICE_PROPERTIES["Build.PRODUCT"]
    build.client = DEVICE_PROPERTIES["Client"]
    build.id = DEVICE_PROPERTIES["Build.ID"]
    build.bootloader = DEVICE_PROPERTIES["Build.BOOTLOADER"]
    build.radio = DEVICE_PROPERTIES["Build.RADIO"]
    build.timestamp = int(time.time() * 1000)
    build.sdkVersion = int(DEVICE_PROPERTIES["Build.VERSION.SDK_INT"])
    build.googleServices = int(DEVICE_PROPERTIES["GSF.version"])

    checkin = AndroidCheckinProto()
    checkin.build.CopyFrom(build)
    checkin.lastCheckinMsec = 0
    checkin.cellOperator = DEVICE_PROPERTIES["CellOperator"]
    checkin.simOperator = DEVICE_PROPERTIES["SimOperator"]
    checkin.roaming = DEVICE_PROPERTIES["Roaming"]
    checkin.userNumber = 0

    device_config = _build_device_config()

    request = AndroidCheckinRequest()
    request.checkin.CopyFrom(checkin)
    request.timeZone = DEVICE_PROPERTIES["TimeZone"]
    request.locale = "en-US"
    request.version = 3
    request.deviceConfiguration.CopyFrom(device_config)

    return request.SerializeToString()


def _build_upload_device_config_request():
    request = UploadDeviceConfigRequest()
    request.deviceConfiguration.CopyFrom(_build_device_config())
    request.manufacturer = DEVICE_PROPERTIES["Build.MANUFACTURER"]
    return request.SerializeToString()


def _build_acquire_request(package_name: str, version_code: int, offer_type: int):
    def encode_varint(value):
        bits = value & 0x7F
        value >>= 7
        result = b""
        while value:
            result += bytes([0x80 | bits])
            bits = value & 0x7F
            value >>= 7
        result += bytes([bits])
        return result

    def encode_field(field_number, wire_type, data):
        tag = (field_number << 3) | wire_type
        return encode_varint(tag) + data

    def encode_varint_field(field_number, value):
        return encode_field(field_number, 0, encode_varint(value))

    def encode_string_field(field_number, value):
        encoded = value.encode("utf-8")
        return encode_field(field_number, 2, encode_varint(len(encoded)) + encoded)

    def encode_length_delimited_field(field_number, sub_message_bytes):
        return encode_field(field_number, 2, encode_varint(len(sub_message_bytes)) + sub_message_bytes)

    payload = b""
    payload += encode_varint_field(2, 1)
    payload += encode_varint_field(3, 3)
    payload += encode_string_field(4, package_name)

    package = b""
    package += encode_length_delimited_field(1, payload)
    package += encode_varint_field(2, 1)

    version = b""
    version += encode_varint_field(1, version_code)
    version += encode_varint_field(3, 0)

    msg30 = b""
    msg30 += encode_varint_field(1, 2)
    msg30 += encode_varint_field(2, 0)

    nonce_bytes = bytes(random.getrandbits(8) for _ in range(256))
    nonce = base64.urlsafe_b64encode(nonce_bytes).rstrip(b"=").decode("ascii")

    request = b""
    request += encode_length_delimited_field(1, package)
    request += encode_length_delimited_field(2, version)
    request += encode_varint_field(15, 0)
    request += encode_varint_field(16, offer_type)
    request += encode_string_field(20, f"nonce={nonce}")
    request += encode_varint_field(25, 2)
    request += encode_length_delimited_field(30, msg30)

    return request


class AuroraDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "aurora"

    def get_versions(self, url: str) -> list[str]:
        # Google Play does not provide a public historical version list.
        return []

    def _init_session(self, dispenser_url: str):
        if not HAS_REQUESTS:
            return None
        session = requests.Session()
        session.headers.update({"User-Agent": "com.aurora.store-4.8.3-75"})
        return session

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path
    ) -> Optional[Path]:
        if not HAS_REQUESTS or not HAS_PROTOBUF:
            missing = []
            if not HAS_REQUESTS:
                missing.append("requests")
            if not HAS_PROTOBUF:
                missing.append("protobuf")
            log_warn(f"[Aurora] Required libraries missing: {', '.join(missing)}. Install with `pip install {' '.join(missing)}`", indent=2)
            return None

        # url can be the dispenser URL or package name
        dispenser_url = url if url.startswith("http") else DEFAULT_DISPENSER_URL
        package_name = output_path.stem.split("_")[0] if "_" in output_path.stem else output_path.stem

        log_info(f"[Aurora] Connecting to dispenser {dispenser_url} for {package_name}...", indent=2)
        session = self._init_session(dispenser_url)

        try:
            # 1. Dispenser credentials
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://auroraoss.com",
                "Referer": "https://auroraoss.com/",
            }
            resp = session.post(dispenser_url, json=DEVICE_PROPERTIES, headers=headers, timeout=30)
            resp.raise_for_status()
            disp_data = resp.json()
            email = disp_data.get("email")
            auth_token = disp_data.get("authToken") or disp_data.get("auth")
            if not email or not auth_token:
                log_warn(f"[Aurora] Dispenser returned invalid data", indent=2)
                return None

            # 2. Checkin
            checkin_bytes = _build_checkin_request()
            checkin_headers = {
                "app": "com.google.android.gms",
                "User-Agent": f"GoogleAuth/1.4 ({DEVICE_PROPERTIES['Build.DEVICE']} {DEVICE_PROPERTIES['Build.ID']})",
                "Content-Type": "application/x-protobuffer",
                "Host": "android.clients.google.com",
            }
            resp = session.post(URL_CHECK_IN, data=checkin_bytes, headers=checkin_headers, timeout=30)
            resp.raise_for_status()
            checkin_resp = AndroidCheckinResponse()
            checkin_resp.ParseFromString(resp.content)
            gsf_id = format(checkin_resp.androidId, "x")
            consistency_token = checkin_resp.deviceCheckinConsistencyToken

            # 3. Headers
            ua = (
                f"Android-Finsky/{DEVICE_PROPERTIES['Vending.versionString']} "
                f"(api=3,versionCode={DEVICE_PROPERTIES['Vending.version']},"
                f"sdk={DEVICE_PROPERTIES['Build.VERSION.SDK_INT']},"
                f"device={DEVICE_PROPERTIES['Build.DEVICE']},"
                f"hardware={DEVICE_PROPERTIES['Build.HARDWARE']},"
                f"product={DEVICE_PROPERTIES['Build.PRODUCT']},"
                f"platformVersionRelease={DEVICE_PROPERTIES['Build.VERSION.RELEASE']},"
                f"model={DEVICE_PROPERTIES['Build.MODEL']},"
                f"buildId={DEVICE_PROPERTIES['Build.ID']},"
                f"isWideScreen=0,supportedAbis={DEVICE_PROPERTIES['Platforms']})"
            )
            default_headers = {
                "Authorization": f"Bearer {auth_token}",
                "User-Agent": ua,
                "X-DFE-Device-Id": gsf_id,
                "Accept-Language": "en-US",
                "X-DFE-Client-Id": "am-android-google",
                "X-DFE-Network-Type": "4",
            }
            if consistency_token:
                default_headers["X-DFE-Device-Checkin-Consistency-Token"] = consistency_token

            # 4. Details
            details_resp = session.get(URL_DETAILS, params={"doc": package_name}, headers=default_headers, timeout=30)
            details_resp.raise_for_status()
            wrapper = ResponseWrapper()
            wrapper.ParseFromString(details_resp.content)
            if not wrapper.HasField("payload") or not wrapper.payload.HasField("detailsResponse"):
                log_warn(f"[Aurora] No detailsResponse for {package_name}", indent=2)
                return None

            item = wrapper.payload.detailsResponse.item
            version_code = item.details.appDetails.versionCode if (item.HasField("details") and item.details.HasField("appDetails")) else 0
            offer_type = item.offer[0].offerType if len(item.offer) > 0 else 1
            if version_code == 0:
                log_warn(f"[Aurora] Could not resolve versionCode for {package_name}", indent=2)
                return None

            # 5. Acquire & Purchase
            try:
                acquire_bytes = _build_acquire_request(package_name, version_code, offer_type)
                acq_headers = dict(default_headers)
                acq_headers["Content-Type"] = "application/x-protobuf"
                session.post(URL_ACQUIRE, data=acquire_bytes, headers=acq_headers, timeout=30)
            except Exception:
                pass

            purchase_resp = session.post(
                URL_PURCHASE,
                params={"ot": str(offer_type), "doc": package_name, "vc": str(version_code)},
                headers=default_headers,
                timeout=30
            )
            purchase_resp.raise_for_status()
            p_wrapper = ResponseWrapper()
            p_wrapper.ParseFromString(purchase_resp.content)
            delivery_token = p_wrapper.payload.buyResponse.encodedDeliveryToken if (p_wrapper.HasField("payload") and p_wrapper.payload.HasField("buyResponse")) else ""
            if not delivery_token:
                log_warn("[Aurora] Failed to obtain delivery token", indent=2)
                return None

            # 6. Delivery
            deliv_resp = session.get(
                URL_DELIVERY,
                params={"ot": str(offer_type), "doc": package_name, "vc": str(version_code), "dtok": delivery_token},
                headers=default_headers,
                timeout=30
            )
            deliv_resp.raise_for_status()
            d_wrapper = ResponseWrapper()
            d_wrapper.ParseFromString(deliv_resp.content)
            if not d_wrapper.HasField("payload") or not d_wrapper.payload.HasField("deliveryResponse"):
                log_warn("[Aurora] No deliveryResponse in payload", indent=2)
                return None

            delivery = d_wrapper.payload.deliveryResponse
            if delivery.status != 1 or not delivery.HasField("appDeliveryData"):
                log_warn(f"[Aurora] Delivery failed with status {delivery.status}", indent=2)
                return None

            app_data = delivery.appDeliveryData
            download_url = app_data.downloadUrl
            splits = [{"name": s.name, "url": s.downloadUrl} for s in app_data.splitDeliveryData]

            # Download payload
            if splits:
                log_info(f"[Aurora] Split APK detected ({len(splits)} splits). Downloading bundle...", indent=2)
                dest_bundle = output_path.parent / f"{output_path.name}.apkm"
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_p = Path(tmp_dir)
                    base_f = tmp_p / "base.apk"
                    r = session.get(download_url, stream=True, timeout=120)
                    r.raise_for_status()
                    base_f.write_bytes(r.content)

                    for s in splits:
                        s_f = tmp_p / s["name"]
                        s_resp = session.get(s["url"], stream=True, timeout=120)
                        s_resp.raise_for_status()
                        s_f.write_bytes(s_resp.content)

                    with zipfile.ZipFile(dest_bundle, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(base_f, "base.apk")
                        for s in splits:
                            zf.write(tmp_p / s["name"], s["name"])

                log_info(f"[Aurora] Saved split bundle to {dest_bundle.name}", indent=2)
                return dest_bundle
            else:
                dest_apk = output_path.parent / f"{output_path.name}.apk"
                log_info(f"[Aurora] Downloading standalone APK to {dest_apk.name}...", indent=2)
                r = session.get(download_url, stream=True, timeout=120)
                r.raise_for_status()
                dest_apk.write_bytes(r.content)
                return dest_apk

        except Exception as e:
            log_warn(f"[Aurora] Download failed: {e}", indent=2)
            return None
