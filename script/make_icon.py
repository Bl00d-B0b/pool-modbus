"""Draw the integration's icon: a pool ladder dipping into water, on a pool-blue tile.

    python script/make_icon.py

Writes custom_components/pool_modbus/brand/icon.png (256 px) and icon@2x.png
(512 px), which Home Assistant shows for the integration. Needs Pillow.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

BRAND = Path(__file__).parent.parent / "custom_components" / "pool_modbus" / "brand"
SIZE = 2048  # drawn large, then scaled down for smooth edges
TOP, BOTTOM = (79, 195, 247), (2, 119, 189)  # light to deep pool blue
WHITE = (255, 255, 255, 255)
FOAM = (255, 255, 255, 150)


def tile() -> Image.Image:
    """A rounded square filled with a top-to-bottom water gradient."""
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True))
        for x in range(SIZE):
            pixels[x, y] = (*color, 255)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE * 0.22, fill=255)
    gradient.putalpha(mask)
    return gradient


def stroke(draw: ImageDraw.ImageDraw, points, width: float, color) -> None:
    """A round-capped line along ``points``, stamped as dots for even edges."""
    r = width / 2
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / 2))
        for i in range(steps + 1):
            x, y = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


WAVE_LEFT, WAVE_RIGHT = SIZE * 0.16, SIZE * 0.84
FRONT_WAVE, BACK_WAVE, AMPLITUDE = SIZE * 0.67, SIZE * 0.80, SIZE * 0.028


def wave_y(x: float, y: float) -> float:
    phase = (x - WAVE_LEFT) / (WAVE_RIGHT - WAVE_LEFT) * 4 * math.pi
    return y + AMPLITUDE * math.sin(phase)


def wave(y: float) -> list[tuple[float, float]]:
    xs = (WAVE_LEFT + (WAVE_RIGHT - WAVE_LEFT) * i / 400 for i in range(401))
    return [(x, wave_y(x, y)) for x in xs]


def handrail(x: float, top: float, radius: float, bottom: float, drop: float):
    """Up the rail, over the edge in a half circle to the left, then a short drop."""
    points = [(x, bottom), (x, top + radius)]
    cx, cy = x - radius, top + radius
    points += [
        (cx + radius * math.cos(a), cy - radius * math.sin(a))
        for a in (math.pi * i / 60 for i in range(61))
    ]
    points.append((x - 2 * radius, top + radius + drop))
    return points


def ladder(draw: ImageDraw.ImageDraw, width: float) -> None:
    """Two handrails curving over the pool edge, joined by two steps."""
    left, right = SIZE * 0.40, SIZE * 0.66
    top, radius = SIZE * 0.20, SIZE * 0.075
    for x in (left, right):  # each rail ends on the front wave, which hides its end
        stroke(draw, handrail(x, top, radius, wave_y(x, FRONT_WAVE), SIZE * 0.07), width, WHITE)
    for y in (SIZE * 0.42, SIZE * 0.54):
        stroke(draw, [(left, y), (right, y)], width * 0.8, WHITE)


def main() -> None:
    image = tile()
    draw = ImageDraw.Draw(image)
    width = SIZE * 0.055
    ladder(draw, width)
    stroke(draw, wave(FRONT_WAVE), width, WHITE)
    foam = Image.new("RGBA", image.size, (0, 0, 0, 0))
    stroke(ImageDraw.Draw(foam), wave(BACK_WAVE), width, FOAM)
    image.alpha_composite(foam)
    BRAND.mkdir(parents=True, exist_ok=True)
    for name, px in (("icon.png", 256), ("icon@2x.png", 512)):
        image.resize((px, px), Image.LANCZOS).save(BRAND / name, optimize=True)
        print(f"wrote {BRAND / name}")


if __name__ == "__main__":
    main()
