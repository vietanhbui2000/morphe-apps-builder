# Keystore & APK Signing Guide

This document explains how APK signing, keystore management, and signature compatibility work in `morphe-apps-builder`.

---

## 1. Overview & Default Configuration

Android requires all installed APKs to be cryptographically signed with an RSA certificate. In `morphe-apps-builder`, APK signing occurs after patching and architecture filtering.

| Setting | Default Value | Description |
| :--- | :--- | :--- |
| **Keystore File** | `keystore.keystore` | Root-level keystore container |
| **Format** | `PKCS12` (PKCS#12) | Standard Java/Android keystore format |
| **Key Alias** | `vietanhbui2000` | Alias identifying the private key entry |
| **Password** | `1234567890` | Keystore and key entry password |

These values are configured under `[general]` in [`config.toml`](config.toml):

```toml
[general]
keystore_file = "keystore.keystore"
keystore_alias = "vietanhbui2000"
keystore_password = "1234567890"
```

---

## 2. Format: PKCS#12 vs Legacy JKS

- **PKCS#12 (`PKCS12`)**: The industry standard format (default in JDK 9+ and Android build tools). Fully supported by modern `apksigner`, `keytool`, and patcher CLIs.
- **JKS (Java KeyStore)**: Legacy proprietary format from older Java versions. Modern `apksigner` on Java 21 rejects JKS by default (`toDerInputStream rejects tag type 0`) unless explicit flags are provided.

`morphe-apps-builder` uses **PKCS#12** by default and includes automatic multi-format detection fallback (`[default, PKCS12, JKS]`) in [`core/apk.py`](core/apk.py) during the signing phase.

---

## 3. Automatic Keystore Generation

If no keystore file exists at the configured path, the build engine automatically creates a new 2048-bit RSA PKCS12 keystore via `keytool`:

```bash
keytool -genkeypair \
  -keystore keystore.keystore \
  -storetype PKCS12 \
  -alias <keystore_alias> \
  -storepass <keystore_password> \
  -keypass <keystore_password> \
  -dname "CN=<keystore_alias>, O=Morphe" \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

---

## 4. Using a Custom Keystore

To use your own release signing key:

1. Generate a PKCS12 keystore:
   ```bash
   keytool -genkeypair \
     -keystore my-release-key.keystore \
     -storetype PKCS12 \
     -alias mykey \
     -storepass mypassword \
     -keypass mypassword \
     -dname "CN=MyName, O=MyOrg" \
     -keyalg RSA \
     -keysize 4096 \
     -validity 10000
   ```
2. Update [`config.toml`](config.toml):
   ```toml
   [general]
   keystore_file = "my-release-key.keystore"
   keystore_alias = "mykey"
   keystore_password = "mypassword"
   ```

---

## 5. Converting Legacy JKS to PKCS#12

If you have an existing legacy `ks.jks` / `ks.keystore` and want to migrate it to PKCS#12 without changing your signing certificate or breaking app update compatibility:

```bash
keytool -importkeystore \
  -srckeystore ks.keystore \
  -srcstoretype JKS \
  -srcstorepass 1234567890 \
  -srcalias vietanhbui2000 \
  -destkeystore keystore.keystore \
  -deststoretype PKCS12 \
  -deststorepass 1234567890 \
  -destkeypass 1234567890 \
  -destalias vietanhbui2000
```

---

## 6. Signature Compatibility & App Updates

- Android requires that an existing installed app and any update APK share the **identical cryptographic certificate**.
- Converting a keystore preserves the exact RSA private key and X.509 certificate. Updating APKs signed with the converted PKCS12 keystore will install over previous releases seamlessly without requiring uninstallation.
