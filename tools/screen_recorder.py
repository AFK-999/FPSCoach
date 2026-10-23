#!/usr/bin/env python3
"""Command-line front end for :mod:`fps_coach.screen_recorder`.

The engine lives in the package (the Capture tab uses it through
``fps_coach.capture_recording``); this script is the developer way to drive it
without the Qt application, for example to check a codec or to watch the
buffering/analysis timing on a real display.

    python tools/screen_recorder.py --duration 10 --analysis-interval 1.5
    python tools/screen_recorder.py --keep --output-dir . --log-level DEBUG

Requires the ``recorder`` extra:  pip install -e ".[recorder]"
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

# Run from a source checkout without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from fps_coach.screen_recorder import (  # noqa: E402 - after the sys.path bootstrap
    ScreenRecorder,
    missing_dependencies,
    mock_analysis_callback,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record the primary monitor in the background, buffer the latest "
            "frame and run a mock analysis callback on a fixed interval."
        )
    )
    parser.add_argument(
        "--duration", type=float, default=0.0, help="seconds to record; 0 = until Ctrl+C"
    )
    parser.add_argument("--fps", type=float, default=15.0, help="capture rate")
    parser.add_argument(
        "--analysis-interval", type=float, default=2.0, help="seconds between analysis calls"
    )
    parser.add_argument(
        "--monitor", type=int, default=1, help="mss monitor index (1 = primary, 0 = all)"
    )
    parser.add_argument("--output-dir", default=None, help="where to put the scratch video")
    parser.add_argument("--keep", action="store_true", help="keep the video instead of deleting it")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
    )
    missing = missing_dependencies()
    if missing:
        print(f"error: missing optional packages: {', '.join(missing)}")
        print('install them with: pip install -e ".[recorder]"')
        return 1

    recorder = ScreenRecorder(
        fps=args.fps,
        analysis_interval=args.analysis_interval,
        monitor_index=args.monitor,
        analysis_callback=mock_analysis_callback,
        output_dir=args.output_dir,
        keep_temp_file=args.keep,
    )

    interrupted = False
    try:
        with recorder:
            print(f"Recording to {recorder.video_path} (Ctrl+C to stop)")
            deadline = time.monotonic() + args.duration if args.duration > 0 else None
            try:
                while recorder.is_running:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
            except KeyboardInterrupt:
                interrupted = True
                print("\nStopping...")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 1

    print(
        f"Done: {recorder.frames_written} frame(s) "
        f"(~{recorder.recorded_seconds:.1f}s), "
        f"{recorder.analysis_invocations} analysis call(s)."
    )
    if recorder.capture_error is not None:
        print(f"Capture error: {recorder.capture_error}")
        return 1
    if recorder.keep_temp_file and recorder.video_path is not None:
        path = recorder.video_path
        if path.exists():
            print(f"Video kept at: {path} ({path.stat().st_size / 1024:.0f} KiB)")
        else:
            print("No video file was produced.")
            return 1
    elif not interrupted:
        print("Scratch video deleted on exit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
