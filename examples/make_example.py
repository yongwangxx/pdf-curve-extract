"""Create a synthetic vector figure with a known curve and a matching legend line."""
from pathlib import Path
import math
import fitz


def main():
    document = fitz.open()
    page = document.new_page(width=480, height=330)
    panel = fitz.Rect(60, 40, 420, 280)
    page.draw_rect(panel, color=(0, 0, 0), width=0.7)
    for tick in range(0, 11, 2):
        x = 60 + 36 * tick
        page.draw_line((x, 280), (x, 275), color=(0, 0, 0))
        page.insert_text((x - 3, 296), str(tick), fontsize=9)
    for tick in range(6):
        y = 280 - 48 * tick
        page.draw_line((60, y), (65, y), color=(0, 0, 0))
        page.insert_text((32, y + 3), f'{tick / 5:.1f}', fontsize=9)
    points = []
    for step in range(101):
        x = step / 10
        y = 1 - math.exp(-x / 3)
        points.append(fitz.Point(60 + 36 * x, 280 - 240 * y))
    page.draw_polyline(points, color=(0.0, 0.3, 0.8), width=1.5)
    page.draw_line((80, 60), (125, 60), color=(0.0, 0.3, 0.8), width=1.5)
    page.insert_text((145, 63), 'response', fontsize=10)
    page.insert_text((205, 320), 'Time (arbitrary units)', fontsize=10)
    document.set_metadata({'title': 'Synthetic vector extraction example'})
    document.save(Path(__file__).with_name('sample.pdf'))
    document.close()
    print('Created sample.pdf: y = 1 - exp(-x / 3).')


if __name__ == '__main__':
    main()
