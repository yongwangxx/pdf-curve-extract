#!/usr/bin/env python3
"""Create a deterministic native raster with visible gaps and a colored annotation."""
from pathlib import Path
import argparse
import math
from PIL import Image, ImageDraw


def make_image(destination):
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 380, 250), outline="black")
    # Deliberate gaps remain absent; no hidden trajectory is supplied to the extractor.
    for start, stop in ((45, 160), (180, 375)):
        points = [(x, round(215-0.35*(x-40)+12*math.sin(x/15))) for x in range(start, stop)]
        draw.line(points, fill=(32, 100, 200), width=3)
    points = [(x, round(85+0.2*(x-40)+8*math.cos(x/19))) for x in range(45, 375)]
    draw.line(points, fill=(210, 65, 40), width=3)
    draw.line((260, 55, 285, 55), fill=(32, 100, 200), width=3)
    draw.text((290, 50), "trace", fill=(32, 100, 200))
    draw.text((195, 275), "x", fill="black")
    draw.text((15, 130), "y", fill="black")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("example image already exists; choose a new output")
    image.save(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent/"raster_sample.png")
    args = parser.parse_args()
    make_image(args.output)
    print("Synthetic raster created.")
