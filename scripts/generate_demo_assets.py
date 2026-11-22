from __future__ import annotations

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.demo_data import SAMPLES  # noqa: E402


OUTPUT = ROOT / "web" / "assets" / "demo"
FONT_DIR = Path("C:/Windows/Fonts")
PALETTES = [
    ("#163c35", "#d26446"),
    ("#254a68", "#c87b34"),
    ("#3e425d", "#cf5f4f"),
    ("#6b5030", "#3b7568"),
    ("#495239", "#c86045"),
    ("#314b52", "#ba7443"),
    ("#2d4650", "#d56548"),
]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    return ImageFont.truetype(str(path), size=size)


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    selected_font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for character in text:
        probe = current + character
        if draw.textlength(probe, font=selected_font) <= max_width:
            current = probe
        else:
            if current:
                lines.append(current)
            current = character
    if current:
        lines.append(current)
    return lines


def generate(sample: dict, index: int) -> None:
    width, height = 1200, 800
    ink, accent = PALETTES[index % len(PALETTES)]
    image = Image.new("RGB", (width, height), "#f4f0e5")
    draw = ImageDraw.Draw(image)
    random.seed(1200 + index)

    # Warm paper and faint classroom-board ruling.
    for y in range(0, height, 46):
        draw.line((62, y, width - 50, y), fill=(115, 126, 114, 30), width=1)
    for _ in range(520):
        x = random.randint(20, width - 20)
        y = random.randint(20, height - 20)
        shade = random.choice((230, 232, 225, 244))
        draw.point((x, y), fill=(shade, shade - 2, shade - 6))

    draw.rectangle((0, 0, width, 15), fill=accent)
    draw.rounded_rectangle((45, 42, width - 45, height - 42), radius=18, outline=(58, 71, 64), width=2)
    draw.line((78, 124, width - 84, 124), fill=ink, width=2)
    draw.line((78, 706, width - 84, 706), fill=(98, 110, 102), width=1)

    small = font("msyh.ttc", 20)
    tiny = font("msyh.ttc", 16)
    title_font = font("simkai.ttf", 62)
    subtitle_font = font("msyh.ttc", 25)
    formula_font = font("segoeui.ttf", 36)

    draw.text((82, 62), f"高等数学 · 课堂板书  {index + 1:02d}", font=small, fill=(70, 82, 75))
    draw.text((width - 245, 62), "LOOM / 匿名合成", font=tiny, fill=(109, 119, 112))
    draw.rounded_rectangle((width - 193, 92, width - 82, 117), radius=13, fill=accent)
    draw.text((width - 181, 97), sample["eyebrow"], font=font("msyh.ttc", 13), fill="white")

    title_lines = wrap_text(draw, sample["title"], title_font, width - 210)
    title_y = 152
    for line in title_lines[:2]:
        draw.text((88, title_y), line, font=title_font, fill=ink, stroke_width=1, stroke_fill=ink)
        title_y += 73

    summary_lines = wrap_text(draw, sample["summary"], subtitle_font, width - 215)
    summary_y = max(282, title_y + 20)
    for line in summary_lines[:3]:
        draw.text((92, summary_y), line, font=subtitle_font, fill=(58, 68, 62))
        summary_y += 38

    formula_box = (104, 420, width - 112, 560)
    draw.rounded_rectangle(formula_box, radius=12, fill="#edf1e9")
    draw.rectangle((104, 420, 112, 560), fill=accent)
    draw.text((138, 440), "关键公式 / KEY IDEA", font=tiny, fill=(89, 103, 94))
    latex = sample["latex"]
    formula_size = 36 if len(latex) < 55 else 28
    formula_font = font("segoeui.ttf", formula_size)
    draw.text((138, 478), latex, font=formula_font, fill=ink)

    note_y = 590
    draw.text((92, note_y), "课堂批注", font=small, fill=accent)
    note_lines = wrap_text(draw, sample["text"], font("simkai.ttf", 30), width - 220)
    for offset, line in enumerate(note_lines[:3]):
        draw.text((94, note_y + 36 + offset * 35), line, font=font("simkai.ttf", 30), fill=(42, 57, 51))

    draw.line((700, 597, width - 111, 597), fill=accent, width=2)
    draw.line((700, 602, width - 111, 602), fill=accent, width=1)
    draw.text((width - 112, 646), f"P.{index + 1:02d}", font=tiny, fill=(105, 116, 109))
    draw.text((80, 730), "样张仅用于功能演示，不含真实课堂与个人信息。", font=tiny, fill=(112, 122, 115))

    image.save(OUTPUT / sample["file"], optimize=True)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(SAMPLES):
        generate(sample, index)
    print(f"generated {len(SAMPLES)} demo images in {OUTPUT}")


if __name__ == "__main__":
    main()
