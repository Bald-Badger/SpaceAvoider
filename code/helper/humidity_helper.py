"""Temperature and humidity helpers for the DHT11 module."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass


DEFAULT_GPIO = 17
DEFAULT_SAMPLES = 1
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_RETRIES = 1
DEFAULT_RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class HumidityReading:
    temperature_c: float
    temperature_f: float
    humidity_percent: float
    gpio: int


def read_humidity_temperature(
    gpio: int = DEFAULT_GPIO,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> HumidityReading:
    """Read one DHT11 temperature/humidity sample from a Raspberry Pi GPIO."""

    dht = create_dht11(gpio)
    try:
        return read_humidity_temperature_from_sensor(
            dht,
            gpio=gpio,
            retries=retries,
            retry_delay=retry_delay,
        )
    finally:
        dht.exit()


def create_dht11(gpio: int = DEFAULT_GPIO):
    """Create a reusable DHT11 sensor object for repeated reads."""

    adafruit_dht, board = _import_dht_dependencies()
    pin = _board_pin_for_gpio(board, gpio)
    return adafruit_dht.DHT11(pin, use_pulseio=False)


def read_humidity_temperature_from_sensor(
    dht,
    gpio: int = DEFAULT_GPIO,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> HumidityReading:
    """Read one sample from an existing DHT11 object."""

    attempts = max(1, retries)

    for attempt in range(attempts):
        try:
            temperature_c = dht.temperature
            humidity = dht.humidity
        except RuntimeError as exc:
            if attempt == attempts - 1:
                raise SystemExit(f"DHT11 read failed after {attempts} attempts: {exc}") from exc
            _reset_failed_dht_cache(dht)
            time.sleep(max(0.0, retry_delay))
            continue

        if temperature_c is None or humidity is None:
            if attempt == attempts - 1:
                raise SystemExit(f"DHT11 returned empty data after {attempts} attempts")
            _reset_failed_dht_cache(dht)
            time.sleep(max(0.0, retry_delay))
            continue

        return HumidityReading(
            temperature_c=float(temperature_c),
            temperature_f=float(temperature_c) * 9.0 / 5.0 + 32.0,
            humidity_percent=float(humidity),
            gpio=gpio,
        )

    raise SystemExit("DHT11 read failed")


def _reset_failed_dht_cache(dht) -> None:
    if hasattr(dht, "_last_called"):
        dht._last_called = 0


def _board_pin_for_gpio(board, gpio: int):
    pin_name = f"D{gpio}"
    try:
        return getattr(board, pin_name)
    except AttributeError as exc:
        raise SystemExit(f"board.{pin_name} is not available on this Raspberry Pi") from exc


def _import_dht_dependencies():
    try:
        import adafruit_dht
        import board
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "adafruit-circuitpython-dht is not installed. Run setup, activate the venv, then try again:\n"
            "  sudo bash scripts/setup_pi_overlay.sh\n"
            "  source .venv/bin/activate"
        ) from exc

    return adafruit_dht, board


def _format_reading(reading: HumidityReading) -> str:
    return (
        f"gpio=GPIO{reading.gpio} "
        f"temperature={reading.temperature_c:.1f} C ({reading.temperature_f:.1f} F) "
        f"humidity={reading.humidity_percent:.1f}%"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read temperature and humidity from a DHT11.")
    parser.add_argument("--gpio", type=int, default=DEFAULT_GPIO, help="BCM GPIO data pin")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="number of samples to print")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS, help="seconds between samples")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="read attempts per sample")
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY_SECONDS,
        help="seconds between retry attempts",
    )
    parser.add_argument("--json", action="store_true", help="print readings as JSON lines")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    samples = max(1, args.samples)
    dht = create_dht11(args.gpio)

    try:
        for index in range(samples):
            reading = read_humidity_temperature_from_sensor(
                dht,
                gpio=args.gpio,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
            if args.json:
                print(json.dumps(asdict(reading), sort_keys=True))
            else:
                print(_format_reading(reading))

            if index < samples - 1:
                time.sleep(max(0.0, args.interval))
    finally:
        dht.exit()


if __name__ == "__main__":
    main()
