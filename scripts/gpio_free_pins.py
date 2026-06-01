#!/usr/bin/env python3
"""Report which Raspberry Pi 40-pin header GPIO pins appear available."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass


HEADER_PINS = (
    (1, None, "3V3", "power"),
    (2, None, "5V", "power"),
    (3, 2, "GPIO2/SDA1", "gpio"),
    (4, None, "5V", "power"),
    (5, 3, "GPIO3/SCL1", "gpio"),
    (6, None, "GND", "ground"),
    (7, 4, "GPIO4", "gpio"),
    (8, 14, "GPIO14/TXD", "gpio"),
    (9, None, "GND", "ground"),
    (10, 15, "GPIO15/RXD", "gpio"),
    (11, 17, "GPIO17", "gpio"),
    (12, 18, "GPIO18/PWM", "gpio"),
    (13, 27, "GPIO27", "gpio"),
    (14, None, "GND", "ground"),
    (15, 22, "GPIO22", "gpio"),
    (16, 23, "GPIO23", "gpio"),
    (17, None, "3V3", "power"),
    (18, 24, "GPIO24", "gpio"),
    (19, 10, "GPIO10/MOSI", "gpio"),
    (20, None, "GND", "ground"),
    (21, 9, "GPIO9/MISO", "gpio"),
    (22, 25, "GPIO25", "gpio"),
    (23, 11, "GPIO11/SCLK", "gpio"),
    (24, 8, "GPIO8/CE0", "gpio"),
    (25, None, "GND", "ground"),
    (26, 7, "GPIO7/CE1", "gpio"),
    (27, 0, "GPIO0/ID_SD", "gpio"),
    (28, 1, "GPIO1/ID_SC", "gpio"),
    (29, 5, "GPIO5", "gpio"),
    (30, None, "GND", "ground"),
    (31, 6, "GPIO6", "gpio"),
    (32, 12, "GPIO12/PWM", "gpio"),
    (33, 13, "GPIO13/PWM", "gpio"),
    (34, None, "GND", "ground"),
    (35, 19, "GPIO19/PCM", "gpio"),
    (36, 16, "GPIO16", "gpio"),
    (37, 26, "GPIO26", "gpio"),
    (38, 20, "GPIO20/PCM", "gpio"),
    (39, None, "GND", "ground"),
    (40, 21, "GPIO21/PCM", "gpio"),
)

RESERVED_GPIO = {
    0: "ID EEPROM pin; avoid for project wiring",
    1: "ID EEPROM pin; avoid for project wiring",
}

ALT_FUNCTIONS = {"SDA1", "SCL1", "TXD0", "RXD0", "TXD1", "RXD1", "SPI0", "MOSI", "MISO", "SCLK"}
GPIOINFO_RE = re.compile(r"line\s+(\d+):\s+\"([^\"]*)\"\s+(.*)")
RASPI_GPIO_RE = re.compile(r"GPIO\s+(\d+):.*?\bfunc=([^\s]+)")
PINCTRL_RE = re.compile(r"^\s*(\d+):.*?//\s+GPIO\d+\s+=\s+([^\s]+)", re.MULTILINE)


@dataclass(frozen=True)
class PinReport:
    physical_pin: int
    bcm: int | None
    name: str
    kind: str
    status: str
    reason: str
    function: str | None = None
    consumer: str | None = None


def main() -> None:
    args = parse_args()
    gpioinfo = read_gpioinfo()
    functions = read_gpio_functions()
    reports = [classify_pin(pin, bcm, name, kind, gpioinfo, functions) for pin, bcm, name, kind in HEADER_PINS]

    if args.json:
        print(json.dumps([asdict(report) for report in reports], indent=2, sort_keys=True))
        return

    print_reports(reports, free_only=args.free_only)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--free-only", action="store_true", help="only print GPIO pins that look free")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def classify_pin(
    physical_pin: int,
    bcm: int | None,
    name: str,
    kind: str,
    gpioinfo: dict[int, str],
    functions: dict[int, str],
) -> PinReport:
    if bcm is None:
        return PinReport(physical_pin, bcm, name, kind, "not-gpio", kind)

    if bcm in RESERVED_GPIO:
        return PinReport(physical_pin, bcm, name, kind, "reserved", RESERVED_GPIO[bcm])

    consumer = gpioinfo.get(bcm)
    function = functions.get(bcm)

    if consumer:
        return PinReport(physical_pin, bcm, name, kind, "used", f"gpioinfo consumer: {consumer}", function, consumer)

    if function and function not in {"INPUT", "OUTPUT", "GPIO"}:
        return PinReport(physical_pin, bcm, name, kind, "used", f"alternate function: {function}", function)

    return PinReport(physical_pin, bcm, name, kind, "free", "no consumer or alternate function detected", function)


def read_gpioinfo() -> dict[int, str]:
    output = run_optional(["gpioinfo"])
    if not output:
        return {}

    consumers: dict[int, str] = {}
    for line in output.splitlines():
        match = GPIOINFO_RE.search(line)
        if not match:
            continue

        bcm = int(match.group(1))
        rest = match.group(3)
        if " unused " in f" {rest} ":
            continue

        quoted = re.findall(r'"([^"]*)"', rest)
        consumer = quoted[0] if quoted else rest.strip()
        if consumer:
            consumers[bcm] = consumer

    return consumers


def read_gpio_functions() -> dict[int, str]:
    return read_raspi_gpio_functions() or read_pinctrl_functions()


def read_raspi_gpio_functions() -> dict[int, str]:
    output = run_optional(["raspi-gpio", "get"])
    if not output:
        return {}

    functions: dict[int, str] = {}
    for match in RASPI_GPIO_RE.finditer(output):
        functions[int(match.group(1))] = normalize_function(match.group(2))
    return functions


def read_pinctrl_functions() -> dict[int, str]:
    output = run_optional(["pinctrl", "get"])
    if not output:
        return {}

    functions: dict[int, str] = {}
    for match in PINCTRL_RE.finditer(output):
        functions[int(match.group(1))] = normalize_function(match.group(2))
    return functions


def normalize_function(function: str) -> str:
    upper = function.upper()
    for prefix in ALT_FUNCTIONS:
        if upper.startswith(prefix):
            return prefix
    if upper in {"INPUT", "OUTPUT", "GPIO"}:
        return upper
    return function


def run_optional(command: list[str]) -> str:
    if shutil.which(command[0]) is None:
        return ""

    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True)
    except OSError:
        return ""

    return result.stdout if result.returncode == 0 else ""


def print_reports(reports: list[PinReport], free_only: bool) -> None:
    rows = [report for report in reports if not free_only or report.status == "free"]
    free = [report for report in reports if report.status == "free"]

    print(f"Free GPIO header pins: {len(free)}")
    if free:
        print(
            "Free BCM GPIOs:",
            ", ".join(f"GPIO{report.bcm}(pin {report.physical_pin})" for report in free),
        )
    print()
    print(f"{'PIN':>3} {'BCM':>5} {'STATUS':<9} {'NAME':<16} {'FUNCTION':<10} REASON")
    print("-" * 86)
    for report in rows:
        bcm = "-" if report.bcm is None else f"GPIO{report.bcm}"
        function = report.function or "-"
        print(
            f"{report.physical_pin:>3} {bcm:>5} {report.status:<9} "
            f"{report.name:<16} {function:<10} {report.reason}"
        )


if __name__ == "__main__":
    main()
