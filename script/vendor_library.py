"""Copy the pool_modbus library into the Home Assistant integration.

HACS installs custom_components/pool_modbus as it is, so the integration
carries its own copy of the library. Run this after changing src/pool_modbus:

    python script/vendor_library.py

tests/test_vendored_library.py fails while the copy is out of date.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "pool_modbus"
TARGET = ROOT / "custom_components" / "pool_modbus" / "library"
SKIP = {"__main__.py"}  # the command-line tool is not needed inside Home Assistant


def library_files() -> list[Path]:
    """The library's source files, relative to the package."""
    return sorted(
        path.relative_to(SOURCE)
        for path in SOURCE.rglob("*.py")
        if path.name not in SKIP and "__pycache__" not in path.parts
    )


def main() -> None:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    files = library_files()
    for relative in files:
        (TARGET / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, TARGET / relative)
    print(f"copied {len(files)} files to {TARGET.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
