"""Resolve the agent home for standalone skill scripts."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from agent_constants import get_agent_home as get_agent_home  # type: ignore[import-not-found]
except (ModuleNotFoundError, ImportError):

    def get_agent_home() -> Path:
        value = os.environ.get("AGENT_HOME", "").strip()
        return Path(value) if value else Path.home() / ".agent"
