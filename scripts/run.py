#!/usr/bin/env python3
"""Launch directly from a source checkout without installing a Python package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tidylinux.__main__ import main

raise SystemExit(main())
