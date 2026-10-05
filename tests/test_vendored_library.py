"""The Home Assistant integration carries an up-to-date copy of the library."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _vendor_script():
    spec = importlib.util.spec_from_file_location(
        "vendor_library", ROOT / "script" / "vendor_library.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_integration_carries_the_current_library() -> None:
    vendor = _vendor_script()
    expected = vendor.library_files()
    copied = sorted(
        path.relative_to(vendor.TARGET)
        for path in vendor.TARGET.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert copied == expected, "run: python script/vendor_library.py"
    stale = [
        relative.as_posix()
        for relative in expected
        if (vendor.SOURCE / relative).read_bytes() != (vendor.TARGET / relative).read_bytes()
    ]
    assert stale == [], "run: python script/vendor_library.py"
