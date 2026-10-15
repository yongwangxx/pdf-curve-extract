"""Create a synthetic plot with known marker centers and paired error-bar caps."""
from pathlib import Path
import fitz

out = Path(__file__).with_name("marker_sample.pdf")
if out.exists():
    raise SystemExit("marker_sample.pdf already exists; preserve it or remove it before regenerating")
doc = fitz.open()
page = doc.new_page(width=320, height=260)
# Draw the selected geometry first so its inventory indices are stable.
page.draw_rect((116, 116, 124, 124), color=None, fill=(0, 0, 1))
page.draw_line((115, 140), (125, 140), color=(0, 0, 1))
page.draw_line((115, 100), (125, 100), color=(0, 0, 1))
ring = page.new_shape()
ring.draw_circle((200, 80), 4)
ring.draw_circle((200, 80), 2.5)
ring.finish(color=None, fill=(0.8, 0, 0), even_odd=True)
ring.commit()
page.draw_line((195, 100), (205, 100), color=(0.8, 0, 0))
page.draw_line((195, 60), (205, 60), color=(0.8, 0, 0))
cross = page.new_shape()
cross.draw_line((196, 156), (204, 164))
cross.draw_line((196, 164), (204, 156))
cross.finish(color=(0, 0.5, 0), closePath=False)
cross.commit()
page.draw_rect((40, 40, 280, 200), color=(0, 0, 0))
for x, label in [(40, "1"), (120, "10"), (200, "100"), (280, "1000")]:
    page.draw_line((x, 200), (x, 205))
    page.insert_text((x-8, 220), label, fontsize=10)
for y, label in [(200, "0.01"), (160, "0.1"), (120, "1"), (80, "10"), (40, "100")]:
    page.draw_line((35, y), (40, y))
    page.insert_text((8, y+3), label, fontsize=9)
page.insert_text((43, 25), "Known marker centers, logarithmic axes", fontsize=11)
page.insert_text((115, 244), "Synthetic x", fontsize=10)
doc.save(out)
doc.close()
print("Created marker_sample.pdf: square (10, 1), ring (100, 10), cross (100, 0.1).")
