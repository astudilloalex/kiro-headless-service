#!/usr/bin/env python3
"""Install the CLI selected by the packaged release manifest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiro_headless_service.infrastructure.installer import main  # noqa: E402

if __name__ == "__main__":
    main()
