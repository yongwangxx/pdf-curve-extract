"""Two-panel inspection previews; native source and numerical exports stay intact."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from matplotlib import font_manager


def save_comparison(original_path, redrawn_path, output_path):
    """Save source-left/reconstruction-right without blending their contents.

    Each preview preserves its aspect ratio. Preview resampling is for display
    only; coordinate exports and native inspection images are not modified.
    """
    destination = Path(output_path)
    if destination.exists():
        raise FileExistsError("comparison output already exists")
    panels = []
    for source in (original_path, redrawn_path):
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            panel = Image.new("RGBA", rgba.size, "white")
            panel.alpha_composite(rgba)
            panel = panel.convert("RGB")
            panels.append(panel)
    # Keep equally sized inspection cells without inventing source resolution.
    bounds = (min(1000, *(image.width for image in panels)),
              min(800, *(image.height for image in panels)))
    for panel in panels:
        panel.thumbnail(bounds, Image.Resampling.LANCZOS)
    panel_width = max(240, *(image.width for image in panels))
    panel_height = max(image.height for image in panels)
    margin, gap, header = 24, 36, 60
    canvas = Image.new("RGB", (2 * panel_width + 2 * margin + gap,
                              panel_height + header + 2 * margin), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 24)
    for index, (panel, label) in enumerate(zip(panels, ("Original", "New plot"))):
        left = margin + index * (panel_width + gap)
        draw.text((left + panel_width / 2, margin + 8), label,
                  fill="#1d2939", font=font, anchor="mt")
        canvas.paste(panel, (left + (panel_width - panel.width) // 2,
                            header + margin + (panel_height - panel.height) // 2))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
