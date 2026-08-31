# Morphe Apps Builder

A modern, modular, and automated APK builder for [Morphe](https://github.com/MorpheApp) patch bundles.

Built with a modular Python 3 engine, resilient multi-source downloaders (with transparent Cloudflare bypass), and clean inline configuration.

---

## Features

- Supports all official and community Morphe `.mpp` patch bundles.
- Builds standalone, signed non-root APKs optimized for size via architecture stripping.
- Resolves compatible stock APK versions automatically from patch bundles.
- Fallback downloader chain: Aurora Store, APKMirror (with FlareSolverr), Uptodown, APKPure, Internet Archive, and Direct URLs.
- Automated daily CI/CD builds with GitHub Releases publishing and changelog tracking.
- Fully declarative configuration in `config.toml` with inline patch options.

---

## Usage

### 1. GitHub Actions (Recommended)

The builder is designed for zero-maintenance CI/CD automation:

1. **Fork or clone** this repository.
2. Customize [`config.toml`](config.toml) with your preferred apps, patches, and architectures.
3. Push changes to `main`:
   - **Automatic Trigger on Config Update**: Pushing edits to `config.toml` automatically starts a new build.
   - **Daily Scheduled Check (`ci.yml`)**: Runs daily at 05:00 UTC+7. If upstream patch repositories have newer releases than your previous release, it triggers the build automatically.
   - **Manual Build (`build.yml`)**: Go to **Actions** $\rightarrow$ **Build & Release APKs** $\rightarrow$ **Run workflow**.
4. Download your patched and signed APKs directly from GitHub **Releases**.

### 2. Local CLI Usage

If you want to build APKs locally:

#### Requirements & Setup
Ensure Java 21+ and Python 3.11+ are installed:
```bash
pip install -r requirements.txt
```

#### Commands
- **Build all enabled apps**:
  ```bash
  python build.py
  ```

- **Build a single app**:
  ```bash
  python build.py --app YouTube
  ```

- **Download stock APKs & prebuilts only**:
  ```bash
  python build.py --download-only
  ```

- **Patch & sign pre-downloaded APKs offline**:
  ```bash
  python build.py --patch-only
  ```

- **Inspect build plan without downloading or patching (Dry Run)**:
  ```bash
  python build.py --dry-run
  ```

- **Check for upstream patch updates**:
  ```bash
  python build.py --check-updates
  ```

- **Clean temporary directories and build artifacts**:
  ```bash
  python build.py --clean
  ```

---

## Configuration Guide (`config.toml`)

See [CONFIG.md](CONFIG.md) for configuration schema and [KEYSTORE.md](KEYSTORE.md) for signing details.

### General Settings
```toml
[general]
default_cli_source = "MorpheApp/morphe-cli"
default_patches_source = "MorpheApp/morphe-patches"
keystore = "keystore.keystore"
keystore_alias = "vietanhbui2000"
keystore_password = "1234567890"
```

### Defining an App
```toml
[YouTube]
enabled = true
id = "com.google.android.youtube"
version = "auto"                         # "auto" (highest compatible), "latest", or pinned (e.g. "21.34.243")
arch = ["all"]                           # ["all"] for universal APK, or ["arm64-v8a", "armeabi-v7a"] for split APKs
aurorastore = true
apkmirror_url = "https://www.apkmirror.com/apk/google-inc/youtube/"
uptodown_url = "https://youtube.en.uptodown.com/android"
apkpure_url = "https://apkpure.com/youtube-app/com.google.android.youtube"
ia_url = "https://archive.org/download/jhc-apks/apks/com.google.android.youtube"
patches_source = "MorpheApp/morphe-patches"
included_patches = [
  "Hide ads",
  "SponsorBlock",
  "Return YouTube Dislike",
  "GmsCore support"
]
excluded_patches = [
  "Custom branding"
]
```

---

## Project Structure

```text
morphe-apps-builder/
├── config.toml                 # Declarative app and patch definitions
├── CONFIG.md                   # Detailed configuration reference and schema
├── keystore.keystore           # Release signing keystore (alias: vietanhbui2000)
├── KEYSTORE.md                 # Keystore management, PKCS12 format & signing guide
├── requirements.txt            # Python dependencies (requests, beautifulsoup4, protobuf)
├── build.py                    # Main CLI orchestrator (download, patch, release.md generation)
│
├── .github/                    # CI/CD automation
│   └── workflows/
│       ├── build.yml           # Automated build and GitHub Release workflow
│       └── ci.yml              # Daily update check workflow
│
├── bin/                        # Binary tools
│   ├── apksigner.jar           # APK signing tool
│   └── APKEditor.jar           # Split APK merger (auto-downloaded)
│
├── core/                       # Core engine utilities
│   ├── config.py               # TOML parser with dotted table flattening
│   ├── apk.py                  # Split APK merging, architecture stripping & apksigner
│   ├── github.py               # GitHub API client (releases, .mpp/jar downloads)
│   ├── http.py                 # HTTP client with FlareSolverr/CFB fallback
│   ├── logger.py               # Structured console & GitHub Actions group logger
│   └── models.py              # Data structures (GeneralConfig, AppConfig, BuildResult)
│
├── downloaders/                # Downloader provider implementations (by fallback priority)
│   ├── base.py                 # Abstract BaseDownloader interface
│   ├── aurorastore.py          # Aurora Store / Google Play downloader
│   ├── apkmirror.py            # APKMirror scraper (bundles & APKs, DPI/arch matching)
│   ├── uptodown.py             # Uptodown scraper (versions API & downloads)
│   ├── apkpure.py              # APKPure downloader
│   ├── ia.py                   # Internet Archive downloader (ia_url)
│   ├── direct.py               # Direct URL template downloader
│   └── aurorastore_pb2.py      # Protobuf definitions for Google Play checkin
│
├── patchers/                   # Patcher provider implementations
│   ├── base.py                 # Abstract BasePatcher interface
│   └── morphe.py               # Morphe engine (version query, options synthesis, patching)
│
└── LICENSE                     # GNU General Public License v3.0
```

---

## Credits

- **[Morphe](https://github.com/MorpheApp)**: For the Morphe CLI, patch framework, and ecosystem.
- **[j-hc/revanced-magisk-module](https://github.com/j-hc/revanced-magisk-module)** by [j-hc](https://github.com/j-hc): Original concepts for automated APK downloading and CI automation.
- **[Revanced-And-Revanced-Extended-Non-Root](https://github.com/FiorenMas/Revanced-And-Revanced-Extended-Non-Root)** by [FiorenMas](https://github.com/FiorenMas): For FlareSolverr scraping and Aurora Store integration concepts.
- **[REAndroid/APKEditor](https://github.com/REAndroid/APKEditor)**: For the split APK merging utility.
- **[AuroraStore](https://gitlab.com/AuroraOSS/AuroraStore)**: For the Google Play token dispenser and checkin protocols.
- **Patch Developers**: For maintaining the patches across various applications.

---

## License

GPL-3.0 License.
