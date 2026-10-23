"""Stress: tight-loop pulls while the worker runs, plus repeated start/stop. Checks every pulled frame."""
import sys
import time

import fpscoach_native as n

cap = n.DesktopCapture()
torn = 0
pulls = 0
for round_no in range(3):
    cap.start(0, 0, -1, -1, 1280, 720, 120)
    end = time.perf_counter() + 2.0
    while time.perf_counter() < end:
        f = cap.latest_frame()
        if f is None:
            continue
        pulls += 1
        px = f["bgra"]
        stride = f["stride"]
        if len(px) != stride * f["height"]:
            torn += 1
            continue
        r_ref = px[2]  # pixel (0, 0): R carries the desktop frame number
        for (x, y) in ((0, 0), (1279, 719), (640, 360), (3, 700)):
            off = y * stride + x * 4
            if px[off + 2] != r_ref or px[off] != ((x + 320) & 255) or px[off + 1] != ((y + 180) & 255):
                torn += 1
                break
        _ = cap.latest_sequence()
    stats = cap.stats()
    print(f"round {round_no}: fps {stats['capture_fps']:.1f}, pulls so far {pulls}, torn {torn}, "
          f"acquire timeouts {stats['acquire_timeouts']}")
    cap.stop()
print("stopped cleanly; torn:", torn)
sys.exit(1 if torn else 0)
