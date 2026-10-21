"""Regenerate the README images in docs/ (needs Pillow: py -m pip install Pillow).

    py tools/make_docs_images.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from crab_pet import pose_params, render  # noqa: E402

DOCS = ROOT / "docs"
FRAME_MS = 120


def sprite(pose, t, scale, look=0):
    img = Image.new("RGBA", (32 * scale, 32 * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for (x, y), c in render(pose_params(pose, t, 1, look)).items():
        d.rectangle([x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1], fill=c)
    return img


def save_gif(frames, path):
    # flatten onto a transparent palette so GitHub shows it on light and dark themes
    out = []
    for f in frames:
        p = f.convert("RGBA")
        alpha = p.getchannel("A")
        q = p.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
        q.paste(255, mask=alpha.point(lambda a: 255 if a < 128 else 0))
        out.append(q)
    out[0].save(path, save_all=True, append_images=out[1:], duration=FRAME_MS, loop=0,
                transparency=255, disposal=2)


def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_menu(d, cx, top, S, hover=None):
    pw, ph = 24 * S, 9 * S
    x0 = cx - pw // 2
    d.rounded_rectangle([x0, top, x0 + pw, top + ph], radius=ph // 2, fill="#262626")
    cy = top + ph // 2
    lx, rx = x0 + pw // 4 + S // 2, x0 + 3 * pw // 4 - S // 2
    hr = int(3.6 * S)
    for which, x in (("left", lx), ("right", rx)):
        if hover == which:
            d.ellipse([x - hr, cy - hr, x + hr, cy + hr], fill="#414141")
    bw, bh, u = int(4.4 * S), int(3.6 * S), max(1, S // 2)
    d.rectangle([lx - bw // 2, cy - bh // 2, lx + bw // 2, cy + bh // 2], outline="#f2f2f2", width=u)
    d.line([lx - bw // 2 + 2 * u, cy - 2 * u, lx - bw // 2 + 4 * u, cy, lx - bw // 2 + 2 * u, cy + 2 * u],
           fill="#f2f2f2", width=u)
    d.line([lx, cy + 2 * u, lx + bw // 2 - 2 * u, cy + 2 * u], fill="#f2f2f2", width=u)
    d.line([x0 + pw // 2, top + 2 * S, x0 + pw // 2, top + ph - 2 * S], fill="#4a4a4a", width=1)
    for i, hgt in enumerate((0.35, 0.7, 1.0, 0.6, 0.35)):
        x = rx + (i - 2) * int(0.9 * S)
        hh = int(hgt * 1.8 * S)
        d.line([x, cy - hh, x, cy + hh], fill="#f2f2f2", width=max(2, S // 2))
    return lx, rx, cy


def draw_bubble(d, text, cx, bottom):
    f = font(22)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    w, h = r - l + 36, b - t + 28
    x0, y0 = cx - w // 2, bottom - h - 14
    d.rectangle([x0, y0, x0 + w, y0 + h], fill="#fffaf3", outline="#4a1608", width=3)
    d.polygon([(cx - 12, y0 + h - 1), (cx + 12, y0 + h - 1), (cx, y0 + h + 14)], fill="#fffaf3")
    d.line([(cx - 12, y0 + h), (cx, y0 + h + 14), (cx + 12, y0 + h)], fill="#4a1608", width=3)
    d.text((x0 + 18 - l, y0 + 14 - t), text, font=f, fill="#2a1a14")


def draw_cursor(d, x, y):
    pts = [(x, y), (x, y + 26), (x + 7, y + 20), (x + 12, y + 30), (x + 16, y + 28), (x + 11, y + 18), (x + 19, y + 18)]
    d.polygon(pts, fill="white", outline="black")


def hero():
    Wd, Ht, S = 960, 460, 8
    frames = []
    seq = [("work", None, None)] * 20 + [("happy", "Claude's all done!", None)] * 14 + \
          [("idle", None, "left")] * 10 + [("idle", None, "right")] * 10
    for t, (pose, say, hover) in enumerate(seq):
        img = Image.new("RGBA", (Wd, Ht), "#1d2130")
        d = ImageDraw.Draw(img)
        # a pretend app window behind the crab
        d.rounded_rectangle([40, 40, 600, 420], radius=14, fill="#f4f1ec")
        d.rounded_rectangle([40, 40, 600, 82], radius=14, fill="#e3ded6")
        d.rectangle([40, 66, 600, 82], fill="#e3ded6")
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            d.ellipse([60 + i * 22, 54, 74 + i * 22, 68], fill=c)
        for i, wdt in enumerate((420, 360, 470, 300, 400, 250, 440, 330)):
            d.rounded_rectangle([72, 112 + i * 36, 72 + wdt, 128 + i * 36], radius=6,
                                fill="#ddd6cc" if i % 3 else "#f6c9b5")
        cx = 760
        img.alpha_composite(sprite(pose, t, S), (cx - 16 * S, 70))
        if say:
            draw_bubble(d, say, cx, 70 + 2 * S)
        if hover:
            lx, rx, cy = draw_menu(d, cx, 70 + 33 * S, S, hover)
            draw_cursor(d, (lx if hover == "left" else rx) + 6, cy + 4)
        frames.append(img)
    frames[0].save(DOCS / "hero.gif", save_all=True, append_images=frames[1:], duration=FRAME_MS, loop=0)


def menu_closeup():
    S = 8
    img = Image.new("RGBA", (32 * S, 44 * S), (0, 0, 0, 0))
    img.alpha_composite(sprite("idle", 0, S), (0, 0))
    d = ImageDraw.Draw(img)
    draw_menu(d, 16 * S, 33 * S, S, "right")
    img.save(DOCS / "menu.png")


def main():
    DOCS.mkdir(exist_ok=True)
    for pose in ("idle", "walk", "sleep", "think", "work", "drag", "happy", "dance", "bubble", "alert"):
        save_gif([sprite(pose, t, 5) for t in range(24)], DOCS / f"{pose}.gif")
    hero()
    menu_closeup()
    print("wrote", sorted(p.name for p in DOCS.iterdir()))


if __name__ == "__main__":
    main()
