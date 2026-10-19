"""Generate the synthetic sample images used by the demo and docs.

Run from the repository root::

    python scripts/make_samples.py

The images are deliberately synthetic: they never depict real people, real
documents, or real receipts, and they match the deterministic mock provider so
the offline demo stays coherent.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "dualsight" / "static" / "samples"
FONT_DIR = Path("C:/Windows/Fonts")


def _font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        names = ["consola.ttf", "CascadiaMono.ttf", "cour.ttf"]
    elif bold:
        names = ["segoeuib.ttf", "arialbd.ttf"]
    else:
        names = ["segoeui.ttf", "arial.ttf"]
    for name in names:
        candidate = FONT_DIR / name
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def _rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]):
    width, height = size
    image = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def make_card() -> None:
    width, height = 1200, 760
    image = _gradient((width, height), (16, 35, 78), (6, 12, 28))
    draw = ImageDraw.Draw(image, "RGBA")
    # soft aurora blobs
    for cx, cy, rx, ry, color in [
        (180, 120, 280, 180, (56, 189, 248, 45)),
        (1040, 640, 320, 220, (139, 92, 246, 50)),
    ]:
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)
        image = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")

    _rounded(draw, (250, 170, 950, 590), 40, (255, 255, 255, 248), (255, 255, 255, 255), 2)
    # eye mark
    draw.ellipse((550, 225, 650, 325), fill=(8, 18, 40, 255))
    draw.ellipse((575, 250, 625, 300), fill=(110, 231, 255, 255))
    draw.ellipse((590, 265, 610, 285), fill=(8, 18, 40, 255))
    _rounded(draw, (520, 350, 680, 362), 6, (110, 231, 255, 255))

    title = _font(86, bold=True)
    subtitle = _font(30)
    caption = _font(22)
    draw.text((600, 405), "DualSight", font=title, fill=(8, 18, 40, 255), anchor="mm")
    draw.text(
        (600, 475),
        "A perception bridge for text-only models",
        font=subtitle,
        fill=(55, 72, 102, 255),
        anchor="mm",
    )
    draw.text(
        (600, 545),
        "vision  ·  evidence  ·  reasoning",
        font=caption,
        fill=(110, 130, 165, 255),
        anchor="mm",
    )
    image.save(OUT / "sample-card.png", optimize=True)


def make_receipt() -> None:
    width, height = 760, 1080
    image = Image.new("RGB", (width, height), (226, 229, 235))
    draw = ImageDraw.Draw(image, "RGBA")
    # soft shadow
    for i in range(18):
        alpha = int(10 * (1 - i / 18))
        draw.rectangle((58 - i, 48 + i, width - 58 + i, height - 40 - i), fill=(30, 40, 60, alpha))
    # paper with a torn bottom edge
    paper = [(72, 40), (width - 72, 40), (width - 72, height - 64)]
    for x in range(width - 72, 72, -18):
        paper.append((x - 9, height - 52))
        paper.append((x - 18, height - 64))
    paper.append((72, height - 64))
    draw.polygon(paper, fill=(255, 253, 247))

    mono = _font(24, mono=True)
    mono_bold = _font(26, mono=True, bold=True)
    mono_large = _font(34, mono=True, bold=True)
    ink = (30, 34, 40)
    muted = (95, 100, 110)
    accent = (13, 116, 144)

    draw.text((width // 2, 100), "DUALSIGHT", font=_font(40, bold=True), fill=ink, anchor="mm")
    draw.text((width // 2, 142), "MOCK MARKET", font=_font(24, bold=True), fill=accent, anchor="mm")
    draw.text((width // 2, 178), "— synthetic receipt —", font=_font(19), fill=muted, anchor="mm")
    draw.line((110, 210, width - 110, 210), fill=(200, 198, 190), width=2)
    draw.text((110, 230), "2026-10-19  14:32", font=mono, fill=muted)
    draw.text((width - 110, 230), "ORDER #MOCK-0042", font=mono, fill=muted, anchor="ra")
    draw.line((110, 270, width - 110, 270), fill=(220, 218, 210), width=2)

    items = [
        ("1x Perception Pass", "$12.00"),
        ("1x Evidence Render", "$6.50"),
        ("1x Cache Hit", "$0.00"),
    ]
    y = 305
    for name, price in items:
        draw.text((110, y), name, font=mono, fill=ink)
        draw.text((width - 110, y), price, font=mono, fill=ink, anchor="ra")
        y += 48
    draw.line((110, y + 8, width - 110, y + 8), fill=(220, 218, 210), width=2)

    y += 34
    for name, price in [("SUBTOTAL", "$18.50"), ("TAX (9.5%)", "$1.76")]:
        draw.text((300, y), name, font=mono, fill=muted)
        draw.text((width - 110, y), price, font=mono, fill=ink, anchor="ra")
        y += 42
    draw.line((110, y + 6, width - 110, y + 6), fill=(30, 34, 40), width=3)
    draw.text((300, y + 24), "TOTAL", font=mono_large, fill=ink)
    draw.text((width - 110, y + 24), "$23.47", font=mono_large, fill=accent, anchor="ra")
    draw.text((width // 2, y + 110), "PAID WITH MOCK CARD", font=mono_bold, fill=muted, anchor="mm")
    draw.text((width // 2, y + 146), "thank you · no real transaction", font=_font(18), fill=muted, anchor="mm")

    # barcode
    bx = 170
    for i, bar in enumerate([2, 5, 2, 8, 3, 2, 6, 2, 4, 8, 2, 3, 5, 2, 7, 3, 2, 6, 4, 2]):
        draw.rectangle((bx, 900, bx + bar, 960), fill=ink)
        bx += bar + 5
    draw.text((width // 2, 982), "MOCK-0042-2026", font=_font(18, mono=True), fill=muted, anchor="mm")

    image.save(OUT / "sample-receipt.png", optimize=True)


def make_chart() -> None:
    width, height = 1100, 700
    image = Image.new("RGB", (width, height), (248, 250, 253))
    draw = ImageDraw.Draw(image, "RGBA")
    card = (60, 42, width - 60, height - 48)
    _rounded(draw, card, 24, (255, 255, 255), (226, 232, 240), 2)

    draw.text((104, 92), "Weekly perception latency", font=_font(34, bold=True), fill=(15, 23, 42))
    draw.text((104, 138), "median end-to-end time, milliseconds", font=_font(19), fill=(100, 116, 139))
    _rounded(draw, (width - 214, 88, width - 104, 132), 22, (224, 242, 254), None)
    draw.text((width - 159, 110), "4 weeks", font=_font(18, bold=True), fill=(3, 105, 161), anchor="mm")

    plot_left, plot_right = 130, width - 120
    plot_top, plot_bottom = 210, 560
    for value in (0, 250, 500, 750, 1000):
        y = plot_bottom - (value / 1000) * (plot_bottom - plot_top)
        draw.line((plot_left, y, plot_right, y), fill=(226, 232, 240), width=1)
        draw.text((plot_left - 18, y), str(value), font=_font(16), fill=(148, 163, 184), anchor="rm")

    bars = [("Week 1", 820), ("Week 2", 640), ("Week 3", 470), ("Week 4", 310)]
    colors = [(14, 116, 144), (13, 148, 136), (16, 185, 129), (52, 211, 153)]
    slot = (plot_right - plot_left) / len(bars)
    for i, ((label, value), color) in enumerate(zip(bars, colors)):
        x0 = plot_left + i * slot + slot * 0.22
        x1 = plot_left + (i + 1) * slot - slot * 0.22
        y0 = plot_bottom - (value / 1000) * (plot_bottom - plot_top)
        _rounded(draw, (x0, y0, x1, plot_bottom), 12, color, None)
        draw.text(((x0 + x1) / 2, y0 - 22), f"{value} ms", font=_font(21, bold=True), fill=(15, 23, 42), anchor="mm")
        draw.text(((x0 + x1) / 2, plot_bottom + 26), label, font=_font(19), fill=(100, 116, 139), anchor="mm")

    draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill=(148, 163, 184), width=2)
    draw.text((plot_left, 620), "Synthetic data · DualSight mock provider", font=_font(17), fill=(148, 163, 184))
    image.save(OUT / "sample-chart.png", optimize=True)


def make_ui() -> None:
    width, height = 1100, 720
    image = Image.new("RGB", (width, height), (241, 245, 249))
    draw = ImageDraw.Draw(image, "RGBA")
    # window
    _rounded(draw, (40, 36, width - 40, height - 36), 18, (255, 255, 255), (203, 213, 225), 2)
    draw.rectangle((40, 36, width - 40, 88), fill=(248, 250, 252))
    for i, color in enumerate([(251, 113, 133), (251, 191, 36), (52, 211, 153)]):
        draw.ellipse((64 + i * 26, 54, 78 + i * 26, 68), fill=color)
    draw.text((width // 2, 62), "DualSight — Settings", font=_font(18), fill=(100, 116, 139), anchor="mm")

    # sidebar
    draw.rectangle((40, 88, 280, height - 36), fill=(248, 250, 252))
    draw.line((280, 88, 280, height - 36), fill=(226, 232, 240), width=1)
    draw.text((72, 122), "WORKSPACE", font=_font(14, bold=True), fill=(148, 163, 184))
    for i, label in enumerate(["Overview", "Providers", "Settings", "Logs"]):
        y = 158 + i * 48
        if label == "Settings":
            _rounded(draw, (60, y - 10, 260, y + 26), 8, (224, 242, 254), None)
            color = (3, 105, 161)
        else:
            color = (71, 85, 105)
        draw.text((80, y), label, font=_font(19, bold=(label == "Settings")), fill=color)
    draw.text((72, 640), "v0.1.0 · mock", font=_font(15), fill=(148, 163, 184))

    # error banner
    _rounded(draw, (312, 112, width - 72, 176), 12, (254, 242, 242), (254, 202, 202), 1)
    draw.ellipse((332, 132, 356, 156), fill=(239, 68, 68))
    draw.text((340, 144), "!", font=_font(18, bold=True), fill=(255, 255, 255), anchor="mm")
    draw.text((372, 130), "API key is missing", font=_font(20, bold=True), fill=(153, 27, 27))
    draw.text(
        (372, 156),
        "Add DUALSIGHT_VISION_API_KEY to continue.",
        font=_font(17),
        fill=(185, 28, 28),
    )

    draw.text((312, 210), "Vision provider", font=_font(21, bold=True), fill=(15, 23, 42))
    draw.text((312, 242), "Used for the perception pass.", font=_font(16), fill=(100, 116, 139))
    _rounded(draw, (312, 276, width - 72, 332), 10, (248, 250, 252), (203, 213, 225), 2)
    draw.text((334, 296), "openai  ·  gpt-4.1-mini", font=_font(18), fill=(51, 65, 85))
    draw.line((width - 112, 298, width - 104, 306), fill=(100, 116, 139), width=2)
    draw.line((width - 104, 306, width - 96, 298), fill=(100, 116, 139), width=2)

    draw.text((312, 366), "API key", font=_font(21, bold=True), fill=(15, 23, 42))
    draw.text((312, 398), "Stored only in your local environment.", font=_font(16), fill=(100, 116, 139))
    _rounded(draw, (312, 432, width - 72, 488), 10, (255, 255, 255), (252, 165, 165), 2)
    draw.text((334, 452), "sk-••••••••••••••••••••", font=_font(18, mono=True), fill=(148, 163, 184))

    draw.text((312, 520), "Default detail", font=_font(18, bold=True), fill=(15, 23, 42))
    for i, label in enumerate(["Brief", "Standard", "Deep"]):
        x = 312 + i * 112
        active = label == "Standard"
        _rounded(draw, (x, 552, x + 96, 592), 20, (224, 242, 254) if active else (248, 250, 252), (186, 230, 253) if active else (203, 213, 225), 1)
        draw.text((x + 48, 572), label, font=_font(16, bold=active), fill=(3, 105, 161) if active else (71, 85, 105), anchor="mm")

    _rounded(draw, (width - 250, height - 112, width - 72, height - 62), 10, (226, 232, 240), None)
    draw.text((width - 161, height - 87), "Save", font=_font(19, bold=True), fill=(148, 163, 184), anchor="mm")
    image.save(OUT / "sample-ui.png", optimize=True)


def make_diagram() -> None:
    width, height = 1200, 700
    image = Image.new("RGB", (width, height), (9, 14, 26))
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(0, width, 40):
        draw.line((x, 0, x, height), fill=(255, 255, 255, 8))
    for y in range(0, height, 40):
        draw.line((0, y, width, y), fill=(255, 255, 255, 8))

    draw.text((width // 2, 76), "How DualSight thinks", font=_font(36, bold=True), fill=(231, 238, 252), anchor="mm")
    draw.text(
        (width // 2, 120),
        "one image  →  structured evidence  →  a text-only answer",
        font=_font(20),
        fill=(147, 164, 193),
        anchor="mm",
    )

    boxes = [
        ("Image", "pixels", (70, 270, 280, 470), (56, 189, 248)),
        ("Vision model", "perception pass", (350, 270, 580, 470), (167, 139, 250)),
        ("Evidence", "bounded text", (650, 270, 850, 470), (110, 231, 255)),
        ("Reasoning model", "your LLM", (920, 270, 1130, 470), (52, 211, 153)),
    ]
    for title, subtitle, box, color in boxes:
        _rounded(draw, box, 22, (17, 26, 43, 230), (*color, 140), 2)
        cx = (box[0] + box[2]) / 2
        draw.text((cx, box[1] + 76), title, font=_font(22, bold=True), fill=(231, 238, 252), anchor="mm")
        draw.text((cx, box[1] + 118), subtitle, font=_font(18), fill=(147, 164, 193), anchor="mm")
        draw.ellipse((cx - 18, box[3] - 62, cx + 18, box[3] - 26), fill=(*color, 70), outline=(*color, 200), width=2)

    for x0, x1 in [(280, 350), (580, 650), (850, 920)]:
        y = 370
        draw.line((x0 + 8, y, x1 - 16, y), fill=(148, 163, 184), width=3)
        draw.polygon([(x1 - 16, y - 9), (x1 - 2, y), (x1 - 16, y + 9)], fill=(148, 163, 184))

    draw.text((width // 2, 552), "The reasoning model never sees pixels.", font=_font(21), fill=(110, 231, 255), anchor="mm")
    draw.text(
        (width // 2, 588),
        "It sees a compact, source-attributed, uncertainty-aware evidence block.",
        font=_font(18),
        fill=(147, 164, 193),
        anchor="mm",
    )
    image.save(OUT / "sample-diagram.png", optimize=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_card()
    make_receipt()
    make_chart()
    make_ui()
    make_diagram()
    for path in sorted(OUT.glob("*.png")):
        with Image.open(path) as image:
            print(f"{path.name:24} {image.width}x{image.height}  {path.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
