"""Display glue for SpaceAvoider.

TODO: Implement the display renderer in C++.

The old Python pygame/framebuffer experiment has intentionally been removed.
Python display code should only be orchestration glue: collect runtime state,
build compact render commands, and hand them to a native C++ renderer. The C++
side should own the performance-sensitive work such as pixel conversion,
dirty-region tracking, framebuffer/DRM/KMS output, double buffering, and any
future animation or text rasterization.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_RENDERER_PATH = Path("build/display_renderer")


@dataclass(frozen=True)
class DisplayCommand:
    """Small Python-to-C++ render command placeholder."""

    command: str
    payload: dict[str, Any]


class DisplayRenderer:
    """Thin wrapper around the future C++ display renderer binary."""

    def __init__(self, renderer_path: Path = DEFAULT_RENDERER_PATH) -> None:
        self.renderer_path = renderer_path

    def render(self, command: DisplayCommand) -> None:
        """Send one render command to the C++ renderer.

        This is intentionally glue only. Do not add framebuffer writes, pygame
        rendering, pixel loops, or other heavy display computation here.
        """

        if not self.renderer_path.exists():
            raise SystemExit(
                "Display renderer is not implemented yet.\n"
                "TODO: build the C++ renderer and place it at "
                f"{self.renderer_path}.\n"
                "Python display_helper.py should remain glue logic only."
            )

        subprocess.run(
            [str(self.renderer_path)],
            input=json.dumps(asdict(command)),
            text=True,
            check=True,
        )


def build_smoke_test_command() -> DisplayCommand:
    """Build a tiny placeholder command for the future renderer."""

    return DisplayCommand(
        command="smoke_test",
        payload={
            "shape": "circle",
            "x": 0.5,
            "y": 0.5,
            "radius": 80,
            "color": [0, 220, 120],
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display glue placeholder; real rendering belongs in the C++ renderer."
    )
    parser.add_argument(
        "--renderer",
        type=Path,
        default=DEFAULT_RENDERER_PATH,
        help="path to the future C++ display renderer binary",
    )
    parser.add_argument("--print-command", action="store_true", help="print the placeholder render command as JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    command = build_smoke_test_command()

    if args.print_command:
        print(json.dumps(asdict(command), indent=2))
        return

    DisplayRenderer(renderer_path=args.renderer).render(command)


if __name__ == "__main__":
    main()
