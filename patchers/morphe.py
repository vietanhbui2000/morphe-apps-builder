#!/usr/bin/env python3
"""
Morphe CLI Patcher: compatible version resolver, options JSON generator, and patch runner.
"""

import json
import re
import shlex
import shutil
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
        cli_path: Path,
        patches_path: Path,
        app_id: str,
        mode: str = "auto"
    ) -> Optional[str]:
        """Query Morphe CLI to find the compatible version for a package from the patch bundle."""
        cmd_prefixes = []
        if mode == "beta":
            for x_flag in ("-x", "--experimental"):
                for p_flag in ("--patches", "-p"):
                    cmd_prefixes.append([x_flag, p_flag])
        for p_flag in ("--patches", "-p"):
            cmd_prefixes.append([p_flag])

        # 1. Try list-versions commands
        for flags in cmd_prefixes:
            cmd = [
                "java", "-jar", str(cli_path),
                "list-versions",
                *flags, str(patches_path),
                "-f", app_id
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and res.stdout:
                    stdout = res.stdout

                    # If mode is auto, prefer "Most common compatible versions" (supporting all/most patches)
                    if mode == "auto" and "Most common compatible versions:" in stdout:
                        lines = stdout.split("Most common compatible versions:", 1)[1].splitlines()
                        common_versions = []
                        max_pcount = None
                        for line in lines:
                            line_s = line.strip()
                            if not line_s or line_s.startswith("Index:") or ":" in line_s:
                                break
                            m = re.match(r"^(\d+(?:\.\d+)+)(?:\s*\(\s*(\d+)\s*(?:patch|patches)?\s*\))?", line_s)
                            if m:
                                ver = m.group(1)
                                count = int(m.group(2)) if m.group(2) else 1
                                if max_pcount is None:
                                    max_pcount = count
                                if count >= max_pcount:
                                    common_versions.append(ver)

                        if common_versions:
                            common_versions.sort(key=_version_key, reverse=True)
                            return common_versions[0]

                    # For "latest", "beta", or fallback if no "Most common" section:
                    matches = re.findall(r"(\d+(\.\d+)+)", stdout)
                    if matches:
                        versions = sorted({m[0] for m in matches}, key=_version_key, reverse=True)
                        return versions[0]
            except Exception:
                pass

        # 2. Fallback to list-patches commands
        list_patches_variants = []
        if mode == "beta":
            list_patches_variants.extend([
                ["java", "-jar", str(cli_path), "list-patches", "-x", "-p", str(patches_path), "-f", app_id, "--with-packages", "--with-versions"],
                ["java", "-jar", str(cli_path), "list-patches", "--experimental", "-p", str(patches_path), "-f", app_id, "--with-packages", "--with-versions"],
            ])
        list_patches_variants.extend([
            ["java", "-jar", str(cli_path), "list-patches", "-p", str(patches_path), "-f", app_id, "--with-packages", "--with-versions"],
            ["java", "-jar", str(cli_path), "list-patches", "-p", str(patches_path), "--with-packages", "--with-versions"],
        ])

        for cmd_list_patches in list_patches_variants:
            try:
                res = subprocess.run(cmd_list_patches, capture_output=True, text=True, check=False)
                if res.returncode == 0 and res.stdout:
                    lines = res.stdout.splitlines()
                    found_pkg = False
                    in_versions = False
                    version_counts: Dict[str, int] = {}
                    all_versions: List[str] = []

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
                                    ver = ver_match.group(1)
                                    version_counts[ver] = version_counts.get(ver, 0) + 1
                                    all_versions.append(ver)

                    if mode == "auto" and version_counts:
                        max_c = max(version_counts.values())
                        top = [v for v, c in version_counts.items() if c == max_c]
                        top.sort(key=_version_key, reverse=True)
                        return top[0]

                    if all_versions:
                        all_versions = sorted(set(all_versions), key=_version_key, reverse=True)
                        return all_versions[0]
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
        cli_path: Path,
        patches_path: Path,
        stock_apk_path: Path,
        output_path: Path,
        app_config: AppConfig,
        keystore_path: Path,
        keystore_alias: str,
        keystore_password: str
    ) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="morphe_patch_"))
        options_path = temp_dir / "options.json"

        cmd = [
            "java", "-jar", str(cli_path),
            "patch",
            "-p", str(patches_path)
        ]

        if app_config.exclusive_patches:
            cmd.append("--exclusive")

        for included in app_config.included_patches:
            cmd.extend(["-e", included])

        for excluded in app_config.excluded_patches:
            cmd.extend(["-d", excluded])

        if app_config.patcher_args:
            cmd.extend(shlex.split(app_config.patcher_args))

        if app_config.options and self._build_options_json(app_config.options, options_path):
            cmd.extend(["--options-file", str(options_path)])

        if keystore_path.is_file():
            cmd.extend([
                "--keystore", str(keystore_path),
                "--keystore-entry-alias", keystore_alias,
                "--keystore-password", keystore_password,
                "--keystore-entry-password", keystore_password,
            ])

        cmd.extend([
            "--force",
            "--continue-on-error",
            "-t", str(temp_dir),
            "-o", str(output_path),
            str(stock_apk_path)
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

            if return_code == 0 and output_path.is_file() and output_path.stat().st_size > 0:
                return True
            else:
                log_error(f"Patcher exited with code {return_code}", indent=2)
                return False
        except Exception as e:
            log_error(f"Execution error during patching: {e}", indent=2)
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

morphe_patcher = MorphePatcher()
