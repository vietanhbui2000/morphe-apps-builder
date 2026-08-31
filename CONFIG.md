# Configuration Guide (`config.toml`)

All apps, patch sources, versions, and patch settings are declaratively defined in [`config.toml`](config.toml).

---

## 1. Quick Start: Adding a New App

Adding an app is as simple as defining its section, package name, and at least one download source:

```toml
[Twitch]
id = "tv.twitch.android.app"
patches_source = "arandomhooman/hoomans-morphe-patches"
apkmirror_url = "https://www.apkmirror.com/apk/twitch-interactive-inc/twitch/"
uptodown_url = "https://twitch.en.uptodown.com/android"
included_patches = [
  "7TV and BTTV emotes",
  "Block live ads",
  "Fix login"
]
```

---

## 2. Global Settings (`[general]`)

The `[general]` table defines fallback defaults for all apps:

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `keystore` | `string` | `"keystore.keystore"` | Path to the keystore file used for signing release APKs. |
| `keystore_alias` | `string` | `"vietanhbui2000"` | Keystore entry alias. |
| `keystore_password` | `string` | `"1234567890"` | Keystore and key password. |
| `default_cli_source` | `string` | `"MorpheApp/morphe-cli"` | Default GitHub repository for Morphe CLI. |
| `default_cli_version` | `string` | `"latest"` | Default Morphe CLI release tag. |
| `default_patches_source` | `string` | `"MorpheApp/morphe-patches"` | Default GitHub repository for `.mpp` patch bundles. |
| `default_patches_version` | `string` | `"latest"` | Default patches release tag. |

```toml
[general]
keystore = "keystore.keystore"
keystore_alias = "vietanhbui2000"
keystore_password = "1234567890"
default_cli_source = "MorpheApp/morphe-cli"
default_cli_version = "latest"
default_patches_source = "MorpheApp/morphe-patches"
default_patches_version = "latest"
```

---

## 3. App Settings (`[AppName]`)

The section header (`[YouTube]`, `[YouTube-Music]`, etc.) directly defines the application's display name and output artifact base name (`output/{AppName}_v{version}_{arch}.apk`).

### Core Properties

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | **Required** | The Android application package identifier (e.g. `com.google.android.youtube`). Used by Morphe CLI to query compatible versions and filter patches. |
| `enabled` | `boolean` | `true` | Set to `false` to temporarily skip building this app. |
| `version` | `string` | `"auto"` | Target version strategy:<br>• `"auto"`: Automatically queries the patch bundle and resolves the highest supported version.<br>• `"latest"`: Downloads the latest upstream version from downloaders.<br>• `"X.Y.Z"`: Pins an exact version (e.g. `"21.18.168"`). |
| `arch` | `list[string]` | `["all"]` | Target architectures to build. Supported values:<br>• `["all"]`: Builds universal APK.<br>• `["arm64-v8a", "armeabi-v7a"]`: Builds individual APKs for each architecture.<br>• `["x86_64"]`, `["x86"]`. |
| `dpi` | `string` | `""` | Optional DPI selector for APKMirror (e.g. `"nodpi"`, `"anydpi"`, `"120-640dpi"`). |

### Patcher & Prebuilts Overrides

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `cli_source` | `string` | `general.default_cli_source` | Override CLI GitHub repository for this specific app. |
| `cli_version` | `string` | `"latest"` | CLI release tag (`"latest"`, `"prerelease"`, or specific tag e.g. `"v1.14.0"`). |
| `patches_source` | `string` | `general.default_patches_source` | Override patches GitHub repository for this specific app. |
| `patches_version` | `string` | `"latest"` | Patch release tag (`"latest"`, `"prerelease"`, or specific tag e.g. `"v1.40.0"`). |

### Download Sources (Fallback Chain)

The builder attempts downloaders in priority order based on which URLs are provided:

| Key | Description |
| :--- | :--- |
| `aurorastore` | `boolean` (`true`/`false`). Primary provider. Downloads directly from Google Play via Aurora Store anonymous token dispenser. |
| `aurorastore_url` | Custom Aurora Store token dispenser URL (defaults to `https://auroraoss.com/api/auth`). |
| `apkmirror_url` | APKMirror category/app URL. Automatically handles bundle merging and Cloudflare challenges. |
| `uptodown_url` | Uptodown app URL. Scrapes version history and resolves direct CDN links. |
| `apkpure_url` | APKPure app URL. Downloads APK or `.xapk` bundles. |
| `ia_url` | Internet Archive directory URL containing pre-uploaded stock APKs. |
| `direct_url` | Direct download URL. Supports `{version}` and `{arch}` template variables. |

### Patch Rules

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `exclusive_patches` | `boolean` | `false` | If `true`, disables all default patches and only applies patches listed in `included_patches`. |
| `included_patches` | `list[string]` | `[]` | List of non-default patches to explicitly enable. |
| `excluded_patches` | `list[string]` | `[]` | List of default patches to explicitly disable. |
| `patcher_args` | `string` | `""` | Arbitrary additional flags passed directly to Morphe CLI. |

---

## 4. Inline Patch Options (`[AppName.options."Patch Name"]`)

Instead of managing separate JSON files, patch options can be defined directly in `config.toml`. The engine dynamically synthesizes them into Morphe's options format during the build step.

```toml
[YouTube]
id = "com.google.android.youtube"
included_patches = [
  "Custom branding name for YouTube",
  "Custom speed"
]

# Configure options for 'Custom branding name for YouTube' patch:
[YouTube.options."Custom branding name for YouTube"]
appName = "Morphe YouTube"

# Configure options for 'Custom speed' patch:
[YouTube.options."Custom speed"]
speedList = "0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0"
```

---

## 5. Full Reference Example

```toml
[general]
keystore = "keystore.keystore"
keystore_alias = "vietanhbui2000"
keystore_password = "1234567890"
default_cli_source = "MorpheApp/morphe-cli"
default_cli_version = "latest"
default_patches_source = "MorpheApp/morphe-patches"
default_patches_version = "latest"

[YouTube]
enabled = true
id = "com.google.android.youtube"
version = "auto"
arch = ["all"]
apkmirror_url = "https://www.apkmirror.com/apk/google-inc/youtube/"
uptodown_url = "https://youtube.en.uptodown.com/android"
ia_url = "https://archive.org/download/jhc-apks/apks/com.google.android.youtube"
cli_source = "MorpheApp/morphe-cli"
cli_version = "latest"
patches_source = "MorpheApp/morphe-patches"
patches_version = "latest"
exclusive_patches = false
included_patches = [
  "Add to queue",
  "GmsCore support",
  "Hide ads",
  "Return YouTube Dislike",
  "SponsorBlock"
]
excluded_patches = [
  "Custom branding",
  "Theme"
]
```
