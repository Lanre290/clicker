"""Regenerates clicker/assets/cursor.png — the ghost-cursor icon used by the
guide overlay. Run directly (`python gen_cursor_asset.py`) whenever the
shape/color needs to change; the app just loads the resulting PNG at
runtime, it doesn't render this itself.

Rendered supersampled then downsampled (Lanczos) for anti-aliased edges,
since Tkinter canvas polygons draw with hard/jagged edges at this size.
The outline is a centered stroke along the triangle's boundary with the
fill drawn on top, covering the inner half — this gives a uniform-width
border regardless of the shape's corners, unlike scaling the polygon
outward from its centroid (which over-thickens at vertices).
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Final (1x) triangle points, for this generator's own math only —
# overlay.py no longer reads HOTSPOT from here. It auto-detects the hotspot
# and line clearance straight from whatever PNG is actually at
# clicker/assets/cursor.png (see _detect_cursor_geometry in overlay.py), so
# swapping this file for a hand-made image needs no code changes.
SHAPE = [(0, 0), (0, 24), (18, 17)]
FILL = (0, 224, 255, 255)      # cyan
OUTLINE = (255, 255, 255, 255)  # white
STROKE_WIDTH = 4  # centered stroke; ~half of this ends up visible outside the fill

MARGIN = 6           # padding around the shape so the outline isn't clipped
SUPERSAMPLE = 4       # render at 4x then downsample for anti-aliasing

HOTSPOT = (MARGIN, MARGIN)  # where the tip lands within the final PNG (informational only)


def generate():
    xs = [p[0] for p in SHAPE]
    ys = [p[1] for p in SHAPE]
    w = int(max(xs) - min(xs) + MARGIN * 2)
    h = int(max(ys) - min(ys) + MARGIN * 2)

    big = Image.new("RGBA", (w * SUPERSAMPLE, h * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)

    pts = [((x + MARGIN) * SUPERSAMPLE, (y + MARGIN) * SUPERSAMPLE) for x, y in SHAPE]
    closed = pts + [pts[0]]

    draw.line(closed, fill=OUTLINE, width=STROKE_WIDTH * SUPERSAMPLE, joint="curve")
    for x, y in closed:
        r = (STROKE_WIDTH * SUPERSAMPLE) / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=OUTLINE)  # round the line joins
    draw.polygon(pts, fill=FILL)

    small = big.resize((w, h), Image.LANCZOS)

    out_dir = Path(__file__).parent / "clicker" / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cursor.png"
    small.save(out_path)
    print(f"wrote {out_path} ({w}x{h}, hotspot={HOTSPOT})")


if __name__ == "__main__":
    generate()
