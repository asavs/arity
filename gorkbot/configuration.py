"""Small read-only configuration helpers shared by runtime and observers."""

from __future__ import annotations

import json
import os
from pathlib import Path


def get_config_value(key: str) -> str | None:
    """Resolve an environment or active ``.gorkbot/config.json`` setting."""

    value = os.environ.get(key)
    if value:
        return value
    for path in (Path(".gorkbot/config.json"), Path.home() / ".gorkbot" / "config.json"):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if key in data and data[key]:
                    return str(data[key])
            except Exception:
                pass
    return None


__all__ = ["get_config_value"]
