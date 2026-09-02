"""Build a short, data-safe product trailer from the public web screenshot.

Optional documentation helper; it is not part of the runtime dependencies.
Run from the repository root after installing Pillow, imageio and imageio-ffmpeg.
"""
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "assets" / "web-studio.png"
OUTPUT = ROOT / "docs" / "assets" / "demo.mp4"


def font(size: int):
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def frame(background: Image.Image, title: str, subtitle: str, progress: int) -> Image.Image:
    image = background.copy().convert("RGB")
    overlay = Image.new("RGBA", image.size, (5, 9, 18, 155))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 92, 1200, 628), radius=28, fill=(10, 17, 32, 218), outline=(105, 85, 210, 230), width=2)
    draw.text((132, 178), title, font=font(54), fill=(248, 250, 252, 255))
    draw.text((136, 260), subtitle, font=font(28), fill=(165, 180, 252, 255))
    draw.text((136, 520), "Web Studio  ·  MCP Server  ·  Codex Skill", font=font(22), fill=(203, 213, 225, 255))
    draw.rounded_rectangle((136, 570, 1064, 580), radius=5, fill=(51, 65, 85, 255))
    draw.rounded_rectangle((136, 570, 136 + int(928 * progress / 100), 580), radius=5, fill=(34, 211, 238, 255))
    return image.convert("RGB")


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing screenshot: {SOURCE}")
    background = Image.open(SOURCE).convert("RGB").resize((1280, 720))
    scenes = [
        ("Novel Workspace MCP", "墨境 · Long-form AI writing, built for continuity", 0),
        ("Plan with confidence", "World-building, characters, chapters and timelines in one state", 35),
        ("Generate safely", "Brief → plan → draft → quality gate → handoff", 65),
        ("Ship anywhere", "Local model or API · Web, MCP and Codex Skill", 100),
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with imageio.get_writer(OUTPUT, fps=24, codec="libx264", quality=8, macro_block_size=1) as writer:
        for index, (title, subtitle, progress) in enumerate(scenes):
            still = frame(background, title, subtitle, progress)
            for _ in range(72 if index in (0, 3) else 60):
                writer.append_data(np.asarray(still))
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
