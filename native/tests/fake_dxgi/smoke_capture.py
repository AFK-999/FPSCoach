"""Smoke test of the real native capture worker (built against the fake DXGI layer).

Each frame's pixels must be internally consistent: pixel (x, y) is (x, y, N, 255) for one desktop
frame N. A torn or mixed buffer (for example a recycled buffer written while in use) would fail.
"""
import random
import sys
import time

import fpscoach_native as n

rng = random.Random(3)
cap = n.DesktopCapture()
print("has latest_sequence:", hasattr(cap, "latest_sequence"))
cap.start(0, 0, -1, -1, 960, 540, 60)
time.sleep(3.0)
st = cap.stats()
print("running:", st["running"], "fps: %.1f" % st["capture_fps"], "frames in 3 s:", st["frames_captured"],
      "region: %dx%d" % (st["capture_width"], st["capture_height"]), "err:", repr(st["last_error"]))

torn = 0
malformed = 0
seen = []
frames_seen = []
for _ in range(400):
    f = cap.latest_frame()
    if f is None:
        time.sleep(0.002)
        continue
    seq = f["sequence"]
    seen.append(seq)
    px = f["bgra"]
    stride, w, h = f["stride"], f["width"], f["height"]
    if len(px) != stride * h or stride != w * 4 or w != 960 or h != 540:
        malformed += 1
        continue
    r_ref = px[7 * stride + 5 * 4 + 2]
    frames_seen.append(r_ref)
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(h)
        off = y * stride + x * 4
        # the region is centred on the 1920x1080 desktop: buffer (x, y) is desktop (x + 480, y + 270)
        if px[off] != ((x + 480) & 255) or px[off + 1] != ((y + 270) & 255) or px[off + 2] != r_ref or px[off + 3] != 255:
            torn += 1
            break
    time.sleep(0.003)

print("pulled:", len(seen), "distinct sequences:", len(set(seen)), "malformed:", malformed, "torn:", torn)
print("sequence monotonic:", all(a <= b for a, b in zip(seen, seen[1:])))
print("latest_sequence():", cap.latest_sequence(), type(cap.latest_sequence()).__name__)
cap.stop()
print("stopped; running:", cap.stats()["running"])
sys.exit(1 if (torn or malformed) else 0)
