"""Generate icon.ico (and icon.png preview) for the pc-agent apps.

A friendly robot face on an indigo->cyan rounded tile. Run once; the build picks up icon.ico:
    .venv\\Scripts\\python make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # draw big, then downscale for smooth edges
HERE = Path(__file__).resolve().parent

TOP = (99, 102, 241)   # indigo
BOT = (6, 182, 212)    # cyan
WHITE = (255, 255, 255, 255)

# Vertical gradient (built as a 1px column, then stretched - fast)
col = Image.new("RGB", (1, S))
for y in range(S):
    t = y / (S - 1)
    col.putpixel((0, y), (int(TOP[0] + (BOT[0] - TOP[0]) * t),
                          int(TOP[1] + (BOT[1] - TOP[1]) * t),
                          int(TOP[2] + (BOT[2] - TOP[2]) * t)))
grad = col.resize((S, S)).convert("RGBA")

# Rounded-tile mask
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
tile = Image.composite(grad, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask)

d = ImageDraw.Draw(tile)
cx = S // 2
# antenna
d.line([(cx, int(S * 0.17)), (cx, int(S * 0.27))], fill=WHITE, width=int(S * 0.022))
d.ellipse([cx - int(S * 0.035), int(S * 0.115), cx + int(S * 0.035), int(S * 0.185)], fill=WHITE)
# head / visor
d.rounded_rectangle([int(S * 0.24), int(S * 0.30), int(S * 0.76), int(S * 0.74)],
                    radius=int(S * 0.11), fill=WHITE)
# eyes
eye_y, er = int(S * 0.50), int(S * 0.058)
for ex in (int(S * 0.40), int(S * 0.60)):
    d.ellipse([ex - er, eye_y - er, ex + er, eye_y + er], fill=(6, 182, 212, 255))
# mouth
d.rounded_rectangle([int(S * 0.41), int(S * 0.615), int(S * 0.59), int(S * 0.655)],
                    radius=int(S * 0.02), fill=(99, 102, 241, 255))

icon = tile.resize((256, 256), Image.LANCZOS)
icon.save(HERE / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
icon.save(HERE / "icon.png")
print("wrote", HERE / "icon.ico")
