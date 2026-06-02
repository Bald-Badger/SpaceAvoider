"""Display glue for SpaceAvoider's native C++ renderer."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


DEFAULT_RENDERER_PATH = Path("build/display_renderer")
DEFAULT_FRAMEBUFFER = Path("/dev/fb0")
DEFAULT_FRAMEBUFFER_SYSFS = Path("/sys/class/graphics/fb0")
DEFAULT_DEMO_SECONDS = 4
DEFAULT_CIRCLE_RADIUS = 80


class DisplayRenderer:
    """Thin wrapper around the C++ display renderer binary."""

    def __init__(self, renderer_path: Path = DEFAULT_RENDERER_PATH) -> None:
        self.renderer_path = renderer_path

    def run_corner_circle_demo(
        self,
        seconds: int = DEFAULT_DEMO_SECONDS,
        radius: int = DEFAULT_CIRCLE_RADIUS,
        framebuffer: Path = DEFAULT_FRAMEBUFFER,
        framebuffer_sysfs: Path = DEFAULT_FRAMEBUFFER_SYSFS,
    ) -> None:
        """Run the native four-corner circle demo."""

        if not self.renderer_path.exists():
            raise SystemExit(
                f"Display renderer is not built yet: {self.renderer_path}\n"
                "Run setup or build it manually:\n"
                "  bash scripts/build_native.sh\n"
                "Python display_helper.py should remain glue logic only."
            )

        subprocess.run(
            [
                str(self.renderer_path),
                "--corner-circle-demo",
                "--seconds",
                str(seconds),
                "--radius",
                str(radius),
                "--framebuffer",
                str(framebuffer),
                "--framebuffer-sysfs",
                str(framebuffer_sysfs),
            ],
            check=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SpaceAvoider display tests through the native C++ renderer."
    )
    parser.add_argument(
        "--renderer",
        type=Path,
        default=DEFAULT_RENDERER_PATH,
        help="path to the C++ display renderer binary",
    )
    parser.add_argument("--seconds", type=int, default=DEFAULT_DEMO_SECONDS, help="demo duration in seconds")
    parser.add_argument("--radius", type=int, default=DEFAULT_CIRCLE_RADIUS, help="circle radius in pixels")
    parser.add_argument("--framebuffer", type=Path, default=DEFAULT_FRAMEBUFFER, help="framebuffer device path")
    parser.add_argument(
        "--framebuffer-sysfs",
        type=Path,
        default=DEFAULT_FRAMEBUFFER_SYSFS,
        help="framebuffer sysfs metadata directory",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    DisplayRenderer(renderer_path=args.renderer).run_corner_circle_demo(
        seconds=args.seconds,
        radius=args.radius,
        framebuffer=args.framebuffer,
        framebuffer_sysfs=args.framebuffer_sysfs,
    )


if __name__ == "__main__":
    main()
