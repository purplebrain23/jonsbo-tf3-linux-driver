#!/usr/bin/env python3
"""
Standalone hardware sanity check - no daemon involved, just the library
directly. Pushes a test image with colored edges + labels to one panel, so
you can (a) confirm your unit responds at all, and (b) figure out which
find_panels() index maps to which physical panel position on your specific
cable routing (this varies by unit).

Edge color code: top=YELLOW, bottom=BLUE, left=RED, right=GREEN.

Usage: python3 examples/send_test_image.py [panel_index]
  panel_index: which find_panels() entry to target (default 0). Run once per
  index (0, 1, 2, ...) to map out your panels.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw, ImageFont
from jonsbo_fan_lib import find_panels, install_sigterm_handler, LOGICAL_W as W, LOGICAL_H as H

STRIPE = 20


def build_test_image():
    img = Image.new("RGB", (W, H), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, STRIPE - 1], fill=(255, 220, 0))       # top = yellow
    d.rectangle([0, H - STRIPE, W - 1, H - 1], fill=(0, 80, 255))    # bottom = blue
    d.rectangle([0, 0, STRIPE - 1, H - 1], fill=(255, 0, 0))         # left = red
    d.rectangle([W - STRIPE, 0, W - 1, H - 1], fill=(0, 220, 0))     # right = green
    font = ImageFont.load_default(size=28)
    d.text((W // 2 - 60, H // 2 - 40), "TOP", fill=(255, 255, 255), font=font)
    d.text((30, 30), "LEFT", fill=(255, 255, 255), font=font)
    d.text((W - 140, H - 60), "RIGHT", fill=(255, 255, 255), font=font)
    return img


def main():
    dev_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    panels = find_panels()
    if not panels:
        print("No panels found - check `lsusb | grep 43a8:0e61` and udev permissions.")
        sys.exit(1)
    if dev_idx >= len(panels):
        print(f"Only {len(panels)} panel(s) found (indices 0..{len(panels) - 1}).")
        sys.exit(1)
    install_sigterm_handler()
    p = panels[dev_idx]
    img = build_test_image()

    print(f"[*] {p.label}: open() ...")
    p.open()
    print(f"[*] {p.label}: warmup(test image) ...")
    commits = p.warmup(img)
    print(f"[*] {p.label}: commits={commits}, releasing handle.")
    p.close()
    print("[*] Check the panel visually: top=yellow, bottom=blue, left=red, right=green expected.")


if __name__ == "__main__":
    main()
