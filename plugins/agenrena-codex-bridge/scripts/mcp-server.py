#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from agenrena_codex_bridge.mcp_server import main  # noqa: E402


if __name__ == "__main__":
    main()
