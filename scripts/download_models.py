#!/usr/bin/env python3
"""Download all official YOLO26 checkpoints used by ProtoLabel."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


MODEL_NAMES = (
    "yolo26n.pt",
    "yolo26s.pt",
    "yolo26m.pt",
    "yolo26l.pt",
    "yolo26x.pt",
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Download all YOLO26 checkpoints required by ProtoLabel."
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=project_root / "models",
        help="Destination directory (default: <project>/models)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download checkpoints again even when they already exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        from ultralytics import YOLO
    except ImportError:
        print(
            "Ultralytics is not installed. Run: "
            "python -m pip install -r backend/requirements.txt",
            file=sys.stderr,
        )
        return 1

    original_cwd = Path.cwd()
    failures: list[str] = []
    try:
        os.chdir(model_dir)
        for name in MODEL_NAMES:
            destination = model_dir / name
            if destination.is_file() and destination.stat().st_size > 0 and not args.force:
                print(f"[skip] {name} already exists")
                continue

            if args.force and destination.exists():
                destination.unlink()

            print(f"[download] {name}")
            try:
                YOLO(name)
                if not destination.is_file() or destination.stat().st_size == 0:
                    raise RuntimeError("download completed but checkpoint file was not created")
                size_mb = destination.stat().st_size / (1024 * 1024)
                print(f"[ok] {destination} ({size_mb:.1f} MiB)")
            except Exception as exc:
                failures.append(name)
                print(f"[error] {name}: {exc}", file=sys.stderr)
    finally:
        os.chdir(original_cwd)

    if failures:
        print(f"Failed models: {', '.join(failures)}", file=sys.stderr)
        return 1

    print(f"All YOLO26 checkpoints are ready in {model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
