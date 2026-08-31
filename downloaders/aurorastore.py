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
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from core.logger import log_info, log_warn
from downloaders.base import BaseDownloader

try:
    from downloaders.aurorastore_pb2 import (
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

X_DFE_ENCODED_TARGETS = (
    "CAESN/qigQYC2AMBFfUbyA7SM5Ij/CvfBoIDgxHqGP8R3xzIBvoQtBKFDZ4HAY4FrwSVMasHBOO2Q8akgYRAQECAQO7AQEpKZ0CnwECAwRrAQYBr9PPAoK7sQMBAQMCBAkIDAgBAwEDBAICBAUZEgMEBAMLAQEBBQEBAcYBARYED+cBfS8CHQEKkAEMMxcBIQoUDwYHIjd3DQ4MFk0JWGYZEREYAQOLAYEBFDMIEYMBAgICAgICOxkCD18LGQKEAcgDBIQBAgGLARkYCy8oBTJlBCUocxQn0QUBDkkGxgNZQq0BZSbeAmIDgAEBOgGtAaMCDAOQAZ4BBIEBKUtQUYYBQscDDxPSARA1oAEHAWmnAsMB2wFyywGLAxol+wImlwOOA80CtwN26A0WjwJVbQEJPAH+BRDeAfkHK/ABASEBCSAaHQemAzkaRiu2Ad8BdXeiAwEBGBUBBN4LEIABK4gB2AFLfwECAdoENq0CkQGMBsIBiQEtiwGgA1zyAUQ4uwS8AwhsvgPyAcEDF27vApsBHaICGhl3GSKxAR8MC6cBAgItmQYG9QIeywLvAeYBDArLAh8HASI4ELICDVmVBgsY/gHWARtcAsMBpALiAdsBA7QBpAJmIArpByn0AyAKBwHTARIHAX8D+AMBcRIBBbEDmwUBMacCHAciNp0BAQF0OgQLJDuSAh54kwFSP0eeAQQ4M5EBQgMEmwFXywFo0gFyWwMcapQBBugBPUW2AVgBKmy3AR6PAbMBGQxrUJECvQR+8gFoWDsYgQNwRSczBRXQAgtRswEW0ALMAREYAUEBIG6yATYCRE8OxgER8gMBvQEDRkwLc8MBTwHZAUOnAXiiBakDIbYBNNcCIUmuArIBSakBrgFHKs0EgwV/G3AD0wE6LgECtQJ4xQFwFbUCjQPkBS6vAQqEAUZF3QIM9wEhCoYCQhXsBCyZArQDugIziALWAdIBlQHwBdUErQE6qQaSA4EEIvYBHir9AQVLmgMCApsCKAwHuwgrENsBAjNYswEVmgIt7QJnN4wDEnta+wGfAcUBxgEtEFXQAQWdAUAeBcwBAQM7rAEJATJ0LENrdh73A6UBhAE+qwEeASxLZUMhDREuH0CGARbd7K0GlQo"
)
X_DFE_PHENOTYPE = (
    "H4sIAAAAAAAAB3OO3KjMAAA0KRNuWXukBkBQkAJ2MhgAZb5u2GCwQZbCH_EJ77QHmgvtDtbv-Z9_H63zXXU0NVPB1odlyGy7751Q3CitlPDvFd8lxhz3tpNmz7P92CFw73zdHU2Ie0Ad2kmR8lxhiErTFLt3RPGfJQHSDy7Clw0bg8kqf2owLokN4SecJTLoSwBnzQSd652_MOf2d1vKBNVedzg4ciPoLz2mQ8efGAgYeLou-l-PXn_7Sa1MfhHuySxt-4esulEDp8Sbq54CPPKjpANW-lkU2IZ0F92LBI-ukCKSptqeq1eXU96LD9nZfhKHdtjSwJqUm_2r6pMHOxk01saVanmNopjX3YxQafC4iC6T55aRbC8nTI98AF_kItIQAJb5EQxnKTO7TZDWnr01HVPxelb9A2OWX6poidMWl16K54kcu_jhXw-JSBQkVcD_fPsLSZu6joIBAAA"
)

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


def _get_default_headers(
    auth_token: str,
    gsf_id: str,
    consistency_token: Optional[str] = None,
    config_token: Optional[str] = None
) -> Dict[str, str]:
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
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "User-Agent": ua,
        "X-DFE-Device-Id": gsf_id,
        "Accept-Language": "en-US",
        "X-DFE-Encoded-Targets": X_DFE_ENCODED_TARGETS,
        "X-DFE-Phenotype": X_DFE_PHENOTYPE,
        "X-DFE-Client-Id": "am-android-google",
        "X-DFE-Network-Type": "4",
        "X-DFE-Content-Filters": "",
        "X-Limit-Ad-Tracking-Enabled": "false",
        "X-Ad-Id": "",
        "X-DFE-UserLanguages": "en_US",
        "X-DFE-Request-Params": "timeoutMs=4000",
    }
    if consistency_token:
        headers["X-DFE-Device-Checkin-Consistency-Token"] = consistency_token
    if config_token:
        headers["X-DFE-Device-Config-Token"] = config_token
    return headers


def _get_token_params(email: str, gsf_id: str, aas_token: str) -> Dict[str, str]:
    return {
        "androidId": gsf_id,
        "sdk_version": DEVICE_PROPERTIES["Build.VERSION.SDK_INT"],
        "Email": email,
        "google_play_services_version": "251333035",
        "device_country": "us",
        "lang": "en",
        "callerSig": "38918a453d07199354f8b19af05ec6562ced5788",
        "app": "com.android.vending",
        "client_sig": "38918a453d07199354f8b19af05ec6562ced5788",
        "callerPkg": "com.google.android.gms",
        "Token": aas_token,
        "oauth2_foreground": "1",
        "token_request_options": "CAA4AVAB",
        "check_email": "1",
        "system_partition": "1",
        "droidguard_results": "null",
        "service": "oauth2:https://www.googleapis.com/auth/googleplay",
    }


class AuroraStoreDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "aurorastore"

    @property
    def display_name(self) -> str:
        return "AuroraStore"

    def get_versions(self, url: str) -> list[str]:
        # Google Play does not provide a public historical version list.
        return []

    def _init_session(self, dispenser_url: str):
        if not HAS_REQUESTS:
            return None
        session = requests.Session()
        session.headers.update({"User-Agent": "com.aurora.store-4.8.3-75"})
        return session

    def _get_flaresolverr_cookies(self, url: str) -> Tuple[Dict[str, str], str]:
        fs_url = os.environ.get("FLARESOLVERR_URL", "http://localhost:8191/v1")
        for target in (url, "https://auroraoss.com/"):
            try:
                payload = json.dumps({
                    "cmd": "request.get",
                    "url": target,
                    "maxTimeout": 60000,
                    "returnOnlyCookies": True
                }).encode("utf-8")
                req = urllib.request.Request(
                    fs_url,
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=70) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                        if data.get("status") == "ok":
                            solution = data.get("solution", {})
                            cookies = {c["name"]: c["value"] for c in solution.get("cookies", []) if "name" in c and "value" in c}
                            ua = solution.get("userAgent", "")
                            if cookies:
                                return cookies, ua
            except Exception:
                pass
        return {}, ""

    def _post_dispenser(self, session: Any, dispenser_url: str) -> Optional[dict]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://auroraoss.com",
            "Referer": "https://auroraoss.com/",
        }
        try:
            resp = session.post(dispenser_url, json=DEVICE_PROPERTIES, headers=headers, timeout=30)
            if resp.status_code == 403:
                log_info("[AuroraStore] Dispenser returned 403, resolving Cloudflare with FlareSolverr...", indent=2)
                cookies, fs_ua = self._get_flaresolverr_cookies(dispenser_url)
                if cookies:
                    for k, v in cookies.items():
                        session.cookies.set(k, v)
                    if fs_ua:
                        headers["User-Agent"] = fs_ua
                    resp = session.post(dispenser_url, json=DEVICE_PROPERTIES, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log_warn(f"[AuroraStore] Dispenser auth failed: {e}", indent=2)
            return None

    def download(
        self,
        url: str,
        version: str,
        arch: str,
        dpi: str,
        output_path: Path,
        app_id: str = ""
    ) -> Optional[Path]:
        if not HAS_REQUESTS or not HAS_PROTOBUF:
            missing = []
            if not HAS_REQUESTS:
                missing.append("requests")
            if not HAS_PROTOBUF:
                missing.append("protobuf")
            log_warn(f"[AuroraStore] Required libraries missing: {', '.join(missing)}. Install with `pip install {' '.join(missing)}`", indent=2)
            return None

        dispenser_url = url if url.startswith("http") else DEFAULT_DISPENSER_URL
        package_name = app_id or (output_path.stem.split("_")[0] if "_" in output_path.stem else output_path.stem)

        log_info(f"[AuroraStore] Connecting to dispenser {dispenser_url} for {package_name}...", indent=2)
        session = self._init_session(dispenser_url)

        try:
            # 1. Dispenser credentials
            disp_data = self._post_dispenser(session, dispenser_url)
            if not disp_data:
                return None

            email = disp_data.get("email")
            aas_token = disp_data.get("authToken") or disp_data.get("auth")
            if not email or not aas_token:
                log_warn("[AuroraStore] Dispenser returned invalid data", indent=2)
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

            # 3. Upload Device Config
            device_config_token = None
            try:
                cfg_bytes = _build_upload_device_config_request()
                cfg_headers = _get_default_headers(auth_token=aas_token, gsf_id=gsf_id, consistency_token=consistency_token)
                cfg_headers["Content-Type"] = "application/x-protobuffer"
                cfg_resp = session.post(URL_UPLOAD_DEVICE_CONFIG, data=cfg_bytes, headers=cfg_headers, timeout=30)
                if cfg_resp.status_code == 200:
                    cfg_wrapper = ResponseWrapper()
                    cfg_wrapper.ParseFromString(cfg_resp.content)
                    if cfg_wrapper.HasField("payload") and cfg_wrapper.payload.HasField("uploadDeviceConfigResponse"):
                        device_config_token = cfg_wrapper.payload.uploadDeviceConfigResponse.uploadDeviceConfigToken
            except Exception:
                pass

            # 4. Generate Google Play OAuth Token
            auth_headers = {
                "app": "com.google.android.gms",
                "User-Agent": f"GoogleAuth/1.4 ({DEVICE_PROPERTIES['Build.DEVICE']} {DEVICE_PROPERTIES['Build.ID']})",
                "device": gsf_id,
            }
            token_params = _get_token_params(email=email, gsf_id=gsf_id, aas_token=aas_token)
            auth_resp = session.post(URL_AUTH, data=token_params, headers=auth_headers, timeout=30)
            auth_resp.raise_for_status()

            play_auth_token = None
            for line in auth_resp.text.splitlines():
                if line.startswith("Auth="):
                    play_auth_token = line[5:].strip()
                    break
            if not play_auth_token:
                for part in auth_resp.text.split("&"):
                    if part.startswith("Auth="):
                        play_auth_token = part[5:].strip()
                        break
            if not play_auth_token:
                play_auth_token = aas_token

            # 5. Default headers for Google Play Store FDFE API
            default_headers = _get_default_headers(
                auth_token=play_auth_token,
                gsf_id=gsf_id,
                consistency_token=consistency_token,
                config_token=device_config_token
            )

            # 6. App Details
            details_resp = session.get(URL_DETAILS, params={"doc": package_name}, headers=default_headers, timeout=30)
            details_resp.raise_for_status()
            wrapper = ResponseWrapper()
            wrapper.ParseFromString(details_resp.content)
            if not wrapper.HasField("payload") or not wrapper.payload.HasField("detailsResponse"):
                log_warn(f"[AuroraStore] No detailsResponse for {package_name}", indent=2)
                return None

            details = wrapper.payload.detailsResponse
            if not details.HasField("item"):
                log_warn(f"[AuroraStore] No item in detailsResponse for {package_name}", indent=2)
                return None

            item = details.item
            version_code = item.details.appDetails.versionCode if (item.HasField("details") and item.details.HasField("appDetails")) else 0
            offer_type = item.offer[0].offerType if len(item.offer) > 0 else 1
            if version_code == 0:
                log_warn(f"[AuroraStore] Could not resolve versionCode for {package_name}", indent=2)
                return None

            # 7. Acquire & Purchase
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
                log_warn("[AuroraStore] Failed to obtain delivery token", indent=2)
                return None

            # 8. Delivery
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
                log_warn("[AuroraStore] No deliveryResponse in payload", indent=2)
                return None

            delivery = d_wrapper.payload.deliveryResponse
            if delivery.status != 1 or not delivery.HasField("appDeliveryData"):
                log_warn(f"[AuroraStore] Delivery failed with status {delivery.status}", indent=2)
                return None

            app_data = delivery.appDeliveryData
            download_url = app_data.downloadUrl
            splits = [{"name": s.name, "url": s.downloadUrl} for s in app_data.splitDeliveryData]

            # 9. Download payload
            if splits:
                log_info(f"[AuroraStore] Split APK detected ({len(splits)} splits). Downloading bundle...", indent=2)
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

                log_info(f"[AuroraStore] Saved split bundle to {dest_bundle.name}", indent=2)
                return dest_bundle
            else:
                dest_apk = output_path.parent / f"{output_path.name}.apk"
                log_info(f"[AuroraStore] Downloading standalone APK to {dest_apk.name}...", indent=2)
                r = session.get(download_url, stream=True, timeout=120)
                r.raise_for_status()
                dest_apk.write_bytes(r.content)
                return dest_apk

        except Exception as e:
            log_warn(f"[AuroraStore] Download failed: {e}", indent=2)
            return None
