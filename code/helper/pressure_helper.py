"""Pressure sensor helpers for the SparkFun Qwiic BMP581."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass


DEFAULT_I2C_ADDRESS: int | None = None
PASCALS_PER_INHG = 3386.389
DEFAULT_STARTUP_DELAY_SECONDS = 0.05
DEFAULT_RETRIES = 5
DEFAULT_RETRY_DELAY_SECONDS = 0.05


@dataclass(frozen=True)
class PressureReading:
    pressure_pa: float
    pressure_hpa: float
    pressure_inhg: float
    temperature_c: float


def read_pressure(
    address: int | None = DEFAULT_I2C_ADDRESS,
    startup_delay: float = DEFAULT_STARTUP_DELAY_SECONDS,
) -> PressureReading:
    """Read one pressure sample from a SparkFun BMP581 over I2C."""

    sensor = _open_sensor(address=address)
    time.sleep(max(0.0, startup_delay))
    return _read_pressure_from_sensor(sensor)


def open_pressure_sensor(address: int | None = DEFAULT_I2C_ADDRESS):
    """Open and initialize a reusable BMP581 sensor object."""

    return _open_sensor(address=address)


def read_pressure_from_sensor(sensor) -> PressureReading:
    """Read one pressure sample from an existing BMP581 sensor object."""

    return _read_pressure_from_sensor(sensor)


def _open_sensor(address: int | None):
    qwiic_bmp581 = _import_qwiic_bmp581()
    sensor = qwiic_bmp581.QwiicBMP581(address=address)

    if not sensor.is_connected():
        address_text = "default address" if address is None else f"0x{address:02x}"
        raise SystemExit(
            f"BMP581 is not connected at {address_text}. Check 3V3/GND/SDA/SCL wiring and I2C."
        )

    if not sensor.begin():
        raise SystemExit("BMP581 was detected, but initialization failed.")

    return sensor


def _read_pressure_from_sensor(sensor) -> PressureReading:
    for _ in range(DEFAULT_RETRIES):
        reading = _read_raw_pressure_from_sensor(sensor)
        if _reading_looks_valid(reading):
            return reading

        time.sleep(DEFAULT_RETRY_DELAY_SECONDS)

    raise SystemExit("BMP581 returned invalid startup data after several retries.")


def _read_raw_pressure_from_sensor(sensor) -> PressureReading:
    data = sensor.get_sensor_data()
    pressure_pa = float(data.pressure)
    temperature_c = float(data.temperature)

    return PressureReading(
        pressure_pa=pressure_pa,
        pressure_hpa=pressure_pa / 100.0,
        pressure_inhg=pressure_pa / PASCALS_PER_INHG,
        temperature_c=temperature_c,
    )


def _reading_looks_valid(reading: PressureReading) -> bool:
    return 30_000.0 <= reading.pressure_pa <= 120_000.0 and -40.0 <= reading.temperature_c <= 85.0


def _import_qwiic_bmp581():
    try:
        import qwiic_bmp581
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "sparkfun-qwiic-bmp581 is not installed. Run setup, activate the venv, then try again:\n"
            "  sudo bash scripts/setup_pi_overlay.sh\n"
            "  source .venv/bin/activate"
        ) from exc

    return qwiic_bmp581


def _format_reading(reading: PressureReading) -> str:
    return (
        f"pressure={reading.pressure_pa:.2f} Pa "
        f"({reading.pressure_hpa:.2f} hPa, {reading.pressure_inhg:.4f} inHg) "
        f"temperature={reading.temperature_c:.2f} C"
    )


def _parse_i2c_address(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("address must be decimal or hex, like 0x47") from exc

    if not 0 <= value <= 0x7F:
        raise argparse.ArgumentTypeError("I2C address must be between 0x00 and 0x7f")

    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read pressure from a SparkFun Qwiic BMP581.")
    parser.add_argument(
        "--address",
        type=_parse_i2c_address,
        default=DEFAULT_I2C_ADDRESS,
        help="I2C address, for example 0x47; defaults to the SparkFun library default",
    )
    parser.add_argument("--samples", type=int, default=1, help="number of samples to print")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=DEFAULT_STARTUP_DELAY_SECONDS,
        help="seconds to wait after BMP581 initialization before reading",
    )
    parser.add_argument("--json", action="store_true", help="print readings as JSON lines")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    samples = max(1, args.samples)
    sensor = _open_sensor(address=args.address)
    time.sleep(max(0.0, args.startup_delay))

    for index in range(samples):
        reading = _read_pressure_from_sensor(sensor)
        if args.json:
            print(json.dumps(asdict(reading), sort_keys=True))
        else:
            print(_format_reading(reading))

        if index < samples - 1:
            time.sleep(max(0.0, args.interval))


if __name__ == "__main__":
    main()
