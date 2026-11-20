"""Compare automatic LOD captures with forced LOD0, LOD1 and LOD2 in Unreal PIE."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path)
    parser.add_argument("prefix", help="Capture prefix including run ID and preview level name")
    parser.add_argument("distances", nargs=3, type=float, help="Camera scales expected to pick LOD0, LOD1 and LOD2")
    options = parser.parse_args()
    rows = []
    for expected, distance in enumerate(options.distances):
        for angle in ("front", "side", "three_quarter"):
            frames = []
            for index in (-1, 0, 1, 2):
                path = options.captures / f"{options.prefix}-{index}-d{distance:.2f}-{angle}.png"
                with Image.open(path) as capture:
                    frames.append(capture.convert("RGB"))
            errors = [sum(ImageStat.Stat(ImageChops.difference(frames[0], target)).mean) / 3 for target in frames[1:]]
            exact = [index for index, error in enumerate(errors) if error == 0]
            rows.append({"distance_scale": distance, "angle": angle, "expected_lod": expected,
                         "exact_matches": exact, "mean_absolute_pixel_errors": errors, "pass": exact == [expected]})
    passed = all(row["pass"] for row in rows)
    print(json.dumps({"status": "pass" if passed else "fail", "matches": rows}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
