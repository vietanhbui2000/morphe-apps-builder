#!/usr/bin/env python3
"""
Morphe CLI Patcher: compatible version resolver, options JSON generator, and patch runner.
"""

import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from core.logger import log_info, log_warn, log_error
from core.models import AppConfig
from patchers.base import BasePatcher

def _version_key(v: str):
    """Sort key for semantic / numeric versions (e.g. '21.18.168' -> [21, 18, 168])."""
    parts = re.findall(r"\d+", v)
    return [int(p) for p in parts] if parts else [0]

class MorphePatcher(BasePatcher):
    @property
    def name(self) -> str:
        return "morphe"

    def get_compatible_version(
        self,
        cli_jar: Path,
        patch_file: Path,
        app_id: str
    ) -> Optional[str]:
        """Query Morphe CLI to find the highest compatible version for a package."""
        # 1. Try list-versions commands (--patches and -p)
        for p_flag in ("--patches", "-p"):
            cmd = [
                "java", "-jar", str(cli_jar),
                "list-versions",
                p_flag, str(patch_file),
                "-f", app_id
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and res.stdout:
                    matches = re.findall(r"(\d+(\.\d+)+)", res.stdout)
                    if matches:
                        versions = list(set([m[0] for m in matches]))
                        versions.sort(key=_version_key, reverse=True)
                        return versions[0]
            except Exception:
                pass

        # 2. Fallback to list-patches commands
        for cmd_list_patches in (
            ["java", "-jar", str(cli_jar), "list-patches", "-p", str(patch_file), "-f", app_id, "--with-packages", "--with-versions"],
            ["java", "-jar", str(cli_jar), "list-patches", "-p", str(patch_file), "--with-packages", "--with-versions"],
        ):
            try:
                res = subprocess.run(cmd_list_patches, capture_output=True, text=True, check=False)
                if res.returncode == 0 and res.stdout:
                    lines = res.stdout.splitlines()
                    found_pkg = False
                    in_versions = False
                    compatible_versions = []

                    for line in lines:
                        line_s = line.strip()
                        if line_s.startswith("Package name:"):
                            curr_pkg = line_s.split(":", 1)[1].strip()
                            found_pkg = (curr_pkg == app_id)
                            in_versions = False
                        elif found_pkg and line_s.startswith("Compatible versions:"):
                            in_versions = True
                        elif in_versions:
                            if not line_s or line_s.startswith("Index:") or line_s.startswith("Name:") or line_s.startswith("Package"):
                                in_versions = False
                            else:
                                ver_match = re.match(r"^(\d+(\.\d+)+)", line_s)
                                if ver_match:
                                    compatible_versions.append(ver_match.group(1))

                    if compatible_versions:
                        compatible_versions = list(set(compatible_versions))
                        compatible_versions.sort(key=_version_key, reverse=True)
                        return compatible_versions[0]
            except Exception:
                pass

        return None

    def _build_options_json(self, options_dict: dict[str, dict[str, Any]], output_path: Path) -> bool:
        """
        Converts config.toml [App.options] dictionary to Morphe options format:
        [
          {
            "patches": {
              "Patch Name": {
                "options": {
                  "optionKey": "optionValue"
                }
              }
            }
          }
        ]
        """
        patches_obj = {}
        for patch_name, patch_opts in options_dict.items():
            if isinstance(patch_opts, dict) and patch_opts:
                patches_obj[patch_name] = {
                    "options": patch_opts
                }

        if not patches_obj:
            return False

        payload = [
            {
              "patches": patches_obj
            }
        ]

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True

    def patch(
        self,
        cli_jar: Path,
        patch_files: list[Path],
        stock_apk: Path,
        output_apk: Path,
        app_config: AppConfig,
        keystore_path: Path,
        keystore_password: str,
        keystore_alias: str
    ) -> bool:
        output_apk.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="morphe_patch_"))
        options_file = temp_dir / "options.json"

        cmd = [
            "java", "-jar", str(cli_jar),
            "patch"
        ]

        for pf in patch_files:
            cmd.extend(["-p", str(pf)])

        if app_config.options and self._build_options_json(app_config.options, options_file):
            cmd.extend(["--options-file", str(options_file)])

        for excluded in app_config.excluded_patches:
            cmd.extend(["-d", excluded])

        for included in app_config.included_patches:
            cmd.extend(["-e", included])

        if app_config.exclusive_patches:
            cmd.append("--exclusive")

        if app_config.patcher_args:
            cmd.extend(shlex.split(app_config.patcher_args))

        if keystore_path.is_file():
            cmd.extend([
                "--keystore", str(keystore_path),
                "--keystore-password", keystore_password,
                "--keystore-entry-password", keystore_password,
                "--keystore-entry-alias", keystore_alias,
                "--signer", keystore_alias
            ])

        cmd.extend([
            "--force",
            "--continue-on-error",
            "-t", str(temp_dir),
            "-o", str(output_apk),
            str(stock_apk)
        ])

        log_info(f"Running Morphe patcher: {' '.join(cmd[:6])} ...", indent=2)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output
            for line in iter(process.stdout.readline, ''):
                line_str = line.strip()
                if not line_str:
                    continue
                if line_str.startswith("INFO:") or line_str.startswith("Applied:"):
                    log_info(line_str, indent=3)
                elif "ERROR" in line_str or "FATAL" in line_str or "Exception" in line_str:
                    log_warn(line_str, indent=3)

            process.stdout.close()
            return_code = process.wait()

            if return_code == 0 and output_apk.is_file() and output_apk.stat().st_size > 0:
                return True
            else:
                log_error(f"Patcher exited with code {return_code}", indent=2)
                return False
        except Exception as e:
            log_error(f"Execution error during patching: {e}", indent=2)
            return False
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

morphe_patcher = MorphePatcher()
