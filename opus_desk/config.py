"""Load configuration and enforce the paper-only safety rules.

The safety check is code, not a prompt: if the config ever says anything
other than paper mode with live orders disabled, the program refuses to start.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class SafetyError(RuntimeError):
    """Raised when configuration would allow anything other than paper trading."""


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so we don't need an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def enforce_safety(cfg: dict[str, Any]) -> None:
    mode = str(cfg.get("mode", "")).lower()
    allow_live = cfg.get("allow_live_orders")
    if mode != "paper":
        raise SafetyError(f"mode must be 'paper', got {mode!r}. Live trading is not supported.")
    if allow_live is not False:
        raise SafetyError("allow_live_orders must be false. Live trading is not supported.")
    # Environment variables can't override safety either.
    env_mode = os.environ.get("MODE")
    if env_mode is not None and env_mode.lower() != "paper":
        raise SafetyError(f"MODE env var must be 'paper', got {env_mode!r}.")
    env_live = os.environ.get("ALLOW_LIVE_ORDERS")
    if env_live is not None and env_live.lower() not in ("false", "0", ""):
        raise SafetyError("ALLOW_LIVE_ORDERS env var must be false.")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    _load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path) as fh:
        cfg = yaml.safe_load(fh)
    enforce_safety(cfg)
    return cfg
