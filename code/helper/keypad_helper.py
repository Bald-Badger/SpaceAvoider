"""4x4 matrix keypad helper using Adafruit CircuitPython MatrixKeypad."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator


DEFAULT_ROW_GPIOS = (27, 22, 23, 24)
DEFAULT_COLUMN_GPIOS = (12, 26, 19, 16)
DEFAULT_KEYMAP = (
    ("1", "2", "3", "A"),
    ("4", "5", "6", "B"),
    ("7", "8", "9", "C"),
    ("*", "0", "#", "D"),
)
DEFAULT_POLL_INTERVAL_SECONDS = 0.01
DEFAULT_DEBOUNCE_SECONDS = 0.05


@dataclass(frozen=True)
class KeyPress:
    key: str
    keys_pressed: tuple[str, ...]
    timestamp: float


@contextmanager
def open_keypad(
    row_gpios: tuple[int, ...] = DEFAULT_ROW_GPIOS,
    column_gpios: tuple[int, ...] = DEFAULT_COLUMN_GPIOS,
    keymap: tuple[tuple[str, ...], ...] = DEFAULT_KEYMAP,
):
    """Open the matrix keypad and release GPIO handles afterward."""

    adafruit_matrixkeypad, board, digitalio = _import_keypad_dependencies()
    row_pins = [_digital_in_out(board, digitalio, gpio) for gpio in row_gpios]
    column_pins = [_digital_in_out(board, digitalio, gpio) for gpio in column_gpios]

    try:
        yield adafruit_matrixkeypad.Matrix_Keypad(row_pins, column_pins, keymap)
    finally:
        for pin in row_pins + column_pins:
            pin.deinit()


def iter_key_presses(
    keypad,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    seconds: float = 0.0,
    stop_event=None,
) -> Iterator[KeyPress]:
    """Yield debounced newly-pressed keys from an Adafruit Matrix_Keypad."""

    started_at = time.monotonic()
    last_seen: tuple[str, ...] = ()
    stable_since = started_at
    emitted_while_held: set[str] = set()

    while (stop_event is None or not stop_event.is_set()) and (
        seconds <= 0.0 or time.monotonic() - started_at < seconds
    ):
        now = time.monotonic()
        current = tuple(str(key) for key in keypad.pressed_keys)

        if current != last_seen:
            last_seen = current
            stable_since = now

        if not current:
            emitted_while_held.clear()
        elif now - stable_since >= debounce_seconds:
            for key in current:
                if key in emitted_while_held:
                    continue

                emitted_while_held.add(key)
                yield KeyPress(key=key, keys_pressed=current, timestamp=time.time())

        if stop_event is None:
            time.sleep(max(0.001, poll_interval))
        else:
            stop_event.wait(max(0.001, poll_interval))


def _digital_in_out(board, digitalio, gpio: int):
    pin_name = f"D{gpio}"
    try:
        board_pin = getattr(board, pin_name)
    except AttributeError as exc:
        raise SystemExit(f"board.{pin_name} is not available on this Raspberry Pi") from exc

    return digitalio.DigitalInOut(board_pin)


def _import_keypad_dependencies():
    try:
        import adafruit_matrixkeypad
        import board
        import digitalio
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "adafruit-circuitpython-matrixkeypad is not installed. Run setup, activate the venv, then try again:\n"
            "  sudo bash scripts/setup_pi_overlay.sh\n"
            "  source .venv/bin/activate"
        ) from exc

    return adafruit_matrixkeypad, board, digitalio


def _parse_gpio_list(value: str) -> tuple[int, ...]:
    try:
        gpios = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("GPIO list must be comma-separated integers") from exc

    if len(gpios) != 4:
        raise argparse.ArgumentTypeError("4x4 keypad GPIO list must contain exactly 4 pins")

    return gpios


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print debounced 4x4 matrix keypad presses in real time.")
    parser.add_argument(
        "--rows",
        type=_parse_gpio_list,
        default=DEFAULT_ROW_GPIOS,
        help="comma-separated BCM GPIO rows, top-to-bottom",
    )
    parser.add_argument(
        "--columns",
        type=_parse_gpio_list,
        default=DEFAULT_COLUMN_GPIOS,
        help="comma-separated BCM GPIO columns, left-to-right",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="seconds between keypad scans",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=DEFAULT_DEBOUNCE_SECONDS,
        help="seconds a key state must stay stable before printing",
    )
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this many seconds; 0 means run forever")
    parser.add_argument("--json", action="store_true", help="print key events as JSON lines")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with open_keypad(row_gpios=args.rows, column_gpios=args.columns) as keypad:
        for press in iter_key_presses(
            keypad,
            poll_interval=args.poll_interval,
            debounce_seconds=args.debounce,
            seconds=args.seconds,
        ):
            if args.json:
                print(json.dumps(asdict(press), sort_keys=True), flush=True)
            else:
                print(press.key, flush=True)


if __name__ == "__main__":
    main()
