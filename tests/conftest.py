"""Pytest bootstrap: make the custom_components package importable.

The integration's own ``__init__.py`` imports Home Assistant, so for these
pure-python unit tests we pre-register the package in ``sys.modules`` with an
empty stub to prevent Python from executing it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "custom_components"

# Namespace parent: custom_components (no __init__.py in HA layout).
if "custom_components" not in sys.modules:
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["custom_components"] = parent

# Stub the integration package __init__ (which imports HA internals).
realme = types.ModuleType("custom_components.realme_scale")
realme.__path__ = [str(PACKAGE_ROOT / "realme_scale")]
realme.__package__ = "custom_components.realme_scale"
sys.modules["custom_components.realme_scale"] = realme

sys.path.insert(0, str(PROJECT_ROOT))
