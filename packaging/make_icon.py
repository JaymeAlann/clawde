"""Draw clawde.ico from the crab sprite (needs Pillow)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from crab_pet import pose_params, render  # noqa: E402

S = 8
img = Image.new("RGBA", (32 * S, 32 * S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
for (x, y), c in render(pose_params("idle", 0)).items():
    d.rectangle([x * S, (y - 4) * S, (x + 1) * S - 1, (y - 3) * S - 1], fill=c)  # nudge up to centre it
out = Path(__file__).with_name("clawde.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
