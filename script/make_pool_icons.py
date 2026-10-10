"""Draw the integration's icon set (``pool:``) and write it to the frontend module.

    python script/make_pool_icons.py          # writes the frontend module pool_icons.js
    python script/make_pool_icons.py preview  # also renders pool_icons_preview.png (Pillow)

The icons are 24 x 24, filled paths like Material Design Icons. The pool is sunk
into the ground, with a parasol beside it; the cover (a terrace that slides over
the pool) is one line at ground level when closed and gone when open.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = (
    Path(__file__).parent.parent
    / "custom_components"
    / "pool_modbus"
    / "frontend"
    / "pool_icons.js"
)


def rect(x: float, y: float, w: float, h: float) -> str:
    x, y, w, h = (round(v, 2) for v in (x, y, w, h))
    return f"M{x} {y}h{w}v{h}h{-w}z"


def ribbon(x0: float, y: float, w: float, n: int, amp: float = 1.0, thick: float = 1.3) -> str:
    """A wavy band of ``n`` bumps, ``w`` wide from ``x0``, ``y`` its upper mid line."""
    step = w / n
    xs = [round(x0 + i * step, 2) for i in range(n + 1)]
    top = f"M{xs[0]} {y}" + "".join(
        f"Q{round(xs[i] + step / 2, 2)} {y - amp} {xs[i + 1]} {y}" for i in range(n)
    )
    bot = f"L{xs[-1]} {y + thick}" + "".join(
        f"Q{round(xs[i] - step / 2, 2)} {y + thick - amp} {xs[i - 1]} {y + thick}"
        for i in range(n, 0, -1)
    )
    return top + bot + "z"


GROUND_LEVEL = 11.6
GROUND = rect(0.5, GROUND_LEVEL, 1.5, 1.4) + rect(17.2, GROUND_LEVEL, 6.3, 1.4)
POOL = (
    rect(1.4, GROUND_LEVEL, 1.5, 21.6 - GROUND_LEVEL)
    + rect(16.2, GROUND_LEVEL, 1.5, 21.6 - GROUND_LEVEL)
    + rect(1.4, 20.1, 16.3, 1.5)
)
WATER = ribbon(3.6, 14.6, 11.9, 4) + ribbon(3.6, 17.4, 11.9, 4)
PARASOL = rect(19.9, 5.2, 1.2, 6.4) + "M15.6 5.6Q20.5 0.4 23.8 5.6Q20.5 4.4 15.6 5.6z"
SCENE = GROUND + POOL + WATER + PARASOL

ICONS = {
    "cover-open": SCENE,
    "cover-closed": SCENE + rect(2.9, GROUND_LEVEL, 13.3, 1.4),
}


def write_module() -> None:
    icons = json.dumps(ICONS, indent=2)
    OUT.write_bytes(
        f"""// The pool: icon set, drawn by script/make_pool_icons.py. Do not edit by hand.
const ICONS = {icons};

window.customIcons = window.customIcons || {{}};
window.customIcons.pool = {{
  getIcon: (name) =>
    Object.hasOwn(ICONS, name)
      ? Promise.resolve({{ path: ICONS[name] }})
      : Promise.reject(new Error(`no icon pool:${{name}}`)),
  getIconList: () => Promise.resolve(Object.keys(ICONS).map((name) => ({{ name }}))),
}};
""".encode()
    )
    print(f"wrote {OUT.relative_to(Path.cwd())}")


def preview(path: Path, scale: int = 10) -> None:
    from PIL import Image, ImageDraw

    token = re.compile(r"([MLQhvz])|(-?\d*\.?\d+)")

    def polygons(d: str) -> list[list[tuple[float, float]]]:
        out: list[list[tuple[float, float]]] = []
        items = [k or n for k, n in token.findall(d)]
        i, pos, cur = 0, (0.0, 0.0), []

        def num() -> float:
            nonlocal i
            i += 1
            return float(items[i - 1])

        while i < len(items):
            c = items[i]
            i += 1
            if c == "M":
                pos = (num(), num())
                cur = [pos]
            elif c == "L":
                pos = (num(), num())
                cur.append(pos)
            elif c == "h":
                pos = (pos[0] + num(), pos[1])
                cur.append(pos)
            elif c == "v":
                pos = (pos[0], pos[1] + num())
                cur.append(pos)
            elif c == "Q":
                cx, cy, x, y = num(), num(), num(), num()
                x0, y0 = pos
                for t in [k / 12 for k in range(1, 13)]:
                    cur.append(
                        (
                            (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x,
                            (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y,
                        )
                    )
                pos = (x, y)
            elif c == "z":
                out.append(cur)
                cur = []
        return out

    s = scale
    image = Image.new("RGB", (24 * s * len(ICONS) + s * (len(ICONS) + 1), 24 * s + 2 * s), "white")
    draw = ImageDraw.Draw(image)
    for n, d in enumerate(ICONS.values()):
        ox = s + n * (24 * s + s)
        for polygon in polygons(d):
            draw.polygon([(ox + x * s, s + y * s) for x, y in polygon], fill="#222")
    image.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    write_module()
    if "preview" in sys.argv[1:]:
        preview(Path("pool_icons_preview.png"))
