"""Shared helpers for reading the existing Home Assistant Sonoff integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sonoff_config(ha_config: Path) -> dict[str, Any]:
    entries_path = ha_config / ".storage" / "core.config_entries"
    entries = json.loads(entries_path.read_text())["data"]["entries"]
    config = next(
        (entry["data"] for entry in entries if entry.get("domain") == "sonoff"),
        None,
    )
    if config is None:
        raise RuntimeError(
            f"no Sonoff integration configuration found in {entries_path}"
        )
    return config


def device_model(device: dict[str, Any]) -> str:
    return device.get("extra", {}).get("model") or device.get("productModel") or ""

