#!/usr/bin/env python3
"""
Structured logger with ANSI colors and GitHub Actions group folding support.
"""

import os

IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"

def log_info(msg: str, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{Colors.CYAN}[*]{Colors.RESET} {msg}", flush=True)

def log_success(msg: str, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{Colors.GREEN}[+]{Colors.RESET} {msg}", flush=True)

def log_warn(msg: str, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{Colors.YELLOW}[!]{Colors.RESET} {msg}", flush=True)

def log_error(msg: str, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{Colors.RED}[-]{Colors.RESET} {msg}", flush=True)

def log_stage(msg: str) -> None:
    print(f"{Colors.BOLD}{Colors.BLUE}==>{Colors.RESET} {Colors.BOLD}{msg}{Colors.RESET}", flush=True)

def log_app_banner(index: int, total: int, name: str, app_id: str) -> None:
    line = "=" * 70
    print(f"{Colors.BOLD}{Colors.CYAN}{line}", flush=True)
    print(f"[{index}/{total}] Processing {name} ({app_id})", flush=True)
    print(f"{line}{Colors.RESET}", flush=True)

def group_start(title: str) -> None:
    if IS_GITHUB_ACTIONS:
        print(f"::group::{title}", flush=True)
    else:
        print(f"\n{Colors.BOLD}{Colors.MAGENTA}--- {title} ---{Colors.RESET}")

def group_end() -> None:
    if IS_GITHUB_ACTIONS:
        print("::endgroup::", flush=True)
