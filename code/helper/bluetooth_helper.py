"""Bluetooth discovery helper backed by BlueZ bluetoothctl."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass


DEFAULT_SCAN_SECONDS = 10.0
DEFAULT_SCAN_TRANSPORT = "auto"
BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS = 5.0
BLUETOOTHCTL_CONNECT_TIMEOUT_SECONDS = 30.0
ADDRESS_LIKE_NAME_RE = re.compile(r"^[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}$")
DEVICE_LINE_RE = re.compile(
    r"^(?:\[[A-Z]+\]\s+)?Device\s+"
    r"(?P<address>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
    r"(?:\s+(?P<name>.+))?$"
)
CHANGED_NAME_RE = re.compile(
    r"^\[CHG\]\s+Device\s+"
    r"(?P<address>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
    r"\s+Name:\s+(?P<name>.+)$"
)
INFO_FIELD_RE = re.compile(r"^\s*(?P<field>Name|Alias|Icon|Class|RSSI|Paired|Connected):\s+(?P<value>.+)$")


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str
    alias: str
    paired: bool | None = None
    connected: bool | None = None
    rssi: int | None = None


@dataclass(frozen=True)
class BluetoothConnectionResult:
    requested_name: str
    found: bool
    connected: bool
    device: BluetoothDevice | None
    message: str


def ensure_device_connected(
    name: str,
    scan_seconds: float = DEFAULT_SCAN_SECONDS,
    transport: str = "bredr",
) -> BluetoothConnectionResult:
    """Find, pair/trust, and connect a named Bluetooth device when possible."""

    _require_bluetoothctl()
    device = find_device_by_name(name=name, scan_seconds=scan_seconds, transport=transport)
    if device is None:
        return BluetoothConnectionResult(
            requested_name=name,
            found=False,
            connected=False,
            device=None,
            message=f"Bluetooth device {name!r} was not found",
        )

    if device.connected is True:
        return BluetoothConnectionResult(
            requested_name=name,
            found=True,
            connected=True,
            device=device,
            message=f"Bluetooth device {device.name!r} is already connected",
        )

    _pair_trust_connect(device.address)
    connected_device = get_device_info(device.address)
    connected = connected_device.connected is True
    return BluetoothConnectionResult(
        requested_name=name,
        found=True,
        connected=connected,
        device=connected_device,
        message=(
            f"Bluetooth device {connected_device.name!r} connected"
            if connected
            else f"Bluetooth device {connected_device.name!r} was found but did not connect"
        ),
    )


def find_device_by_name(
    name: str,
    scan_seconds: float = DEFAULT_SCAN_SECONDS,
    transport: str = "bredr",
) -> BluetoothDevice | None:
    """Find a Bluetooth device by friendly name from cached devices or a scan."""

    needle = name.casefold()

    for device in list_known_devices():
        if needle in device.name.casefold() or needle in device.alias.casefold():
            return device

    for device in list_nearby_devices(scan_seconds=scan_seconds, transport=transport, name_filter=name):
        return device

    return None


def list_known_devices(name_filter: str = "") -> list[BluetoothDevice]:
    """Return devices currently known to BlueZ, without starting discovery."""

    _require_bluetoothctl()
    output = _run_bluetoothctl_command(("devices",), timeout=BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS)
    devices = _enrich_devices(_parse_devices(output))

    if name_filter:
        needle = name_filter.casefold()
        devices = [
            device
            for device in devices
            if needle in device.name.casefold() or needle in device.alias.casefold()
        ]

    return devices


def get_device_info(address: str) -> BluetoothDevice:
    """Return the latest BlueZ info for one Bluetooth address."""

    placeholder = BluetoothDevice(address=address.upper(), name="unknown", alias="unknown")
    return _merge_device_info(placeholder, _run_bluetoothctl_info(address))


def list_nearby_devices(
    scan_seconds: float = DEFAULT_SCAN_SECONDS,
    transport: str = DEFAULT_SCAN_TRANSPORT,
    name_filter: str = "",
    named_only: bool = False,
) -> list[BluetoothDevice]:
    """Scan for nearby Bluetooth devices and return a deduplicated list."""

    _require_bluetoothctl()
    output = _run_bluetoothctl_scan(scan_seconds=scan_seconds, transport=transport)
    devices = _parse_devices(output)
    devices = _enrich_devices(devices)

    if name_filter:
        needle = name_filter.casefold()
        devices = [
            device
            for device in devices
            if needle in device.name.casefold() or needle in device.alias.casefold()
        ]

    if named_only:
        devices = [
            device
            for device in devices
            if device.name != "unknown" and not ADDRESS_LIKE_NAME_RE.match(device.name)
        ]

    return devices


def _require_bluetoothctl() -> None:
    if shutil.which("bluetoothctl") is None:
        raise SystemExit(
            "bluetoothctl is not installed. Run setup after disabling overlay protection:\n"
            "  sudo bash scripts/setup_pi_overlay.sh"
        )


def _run_bluetoothctl_scan(scan_seconds: float, transport: str) -> str:
    scan_seconds = max(1.0, float(scan_seconds))
    scan_command = _scan_command_for_transport(transport)

    try:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"Could not start bluetoothctl: {exc}") from exc

    assert process.stdin is not None

    try:
        process.stdin.write(f"power on\n{scan_command}\n")
        process.stdin.flush()
        time.sleep(scan_seconds)
        process.stdin.write("scan off\ndevices\nquit\n")
        process.stdin.flush()
    except BrokenPipeError as exc:
        output, _ = process.communicate(timeout=BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS)
        raise SystemExit(_format_bluetoothctl_failure(output)) from exc

    try:
        output, _ = process.communicate(timeout=scan_seconds + BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        output, _ = process.communicate()
        raise SystemExit(_format_bluetoothctl_failure(output)) from exc

    if process.returncode not in (0, None):
        raise SystemExit(_format_bluetoothctl_failure(output))

    if "No default controller available" in output:
        raise SystemExit(
            "No Bluetooth controller is available. Check that Bluetooth is enabled and not blocked by rfkill."
        )

    return output


def _scan_command_for_transport(transport: str) -> str:
    if transport == "auto":
        return "scan on"

    if transport in {"bredr", "le"}:
        return f"scan {transport}"

    raise ValueError(f"Unsupported Bluetooth scan transport: {transport}")


def _format_bluetoothctl_failure(output: str) -> str:
    details = output.strip()
    if details:
        return f"bluetoothctl scan failed:\n{details}"

    return "bluetoothctl scan failed without output."


def _parse_devices(output: str) -> list[BluetoothDevice]:
    devices_by_address: dict[str, BluetoothDevice] = {}

    for raw_line in output.splitlines():
        line = _strip_bluetooth_prompt(raw_line.strip())
        if not line:
            continue

        match = CHANGED_NAME_RE.match(line) or DEVICE_LINE_RE.match(line)
        if not match:
            continue

        address = match.group("address").upper()
        name = (match.group("name") or "").strip() or "unknown"

        current = devices_by_address.get(address)
        if current is None or current.name == "unknown":
            devices_by_address[address] = BluetoothDevice(address=address, name=name, alias=name)

    return sorted(devices_by_address.values(), key=lambda device: (device.name.lower(), device.address))


def _enrich_devices(devices: list[BluetoothDevice]) -> list[BluetoothDevice]:
    enriched_devices = []

    for device in devices:
        info = _run_bluetoothctl_info(device.address)
        enriched_devices.append(_merge_device_info(device, info))

    return sorted(enriched_devices, key=lambda device: (device.name.lower(), device.address))


def _run_bluetoothctl_info(address: str) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", "info", address],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    return result.stdout


def _run_bluetoothctl_commands(commands: tuple[str, ...], timeout: float) -> str:
    return _run_bluetoothctl_command((*commands, "quit"), timeout=timeout)


def _pair_trust_connect(address: str) -> str:
    steps = (
        ("power on", 0.5),
        ("agent on", 0.5),
        ("default-agent", 0.5),
        (f"pair {address}", 8.0),
        (f"trust {address}", 1.0),
        (f"connect {address}", 8.0),
        (f"info {address}", 0.5),
        ("quit", 0.0),
    )

    try:
        process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        raise SystemExit(f"Could not start bluetoothctl: {exc}") from exc

    assert process.stdin is not None

    for command, delay_seconds in steps:
        try:
            process.stdin.write(f"{command}\n")
            process.stdin.flush()
        except BrokenPipeError as exc:
            output, _ = process.communicate(timeout=BLUETOOTHCTL_TIMEOUT_PADDING_SECONDS)
            raise SystemExit(_format_bluetoothctl_failure(output)) from exc

        time.sleep(delay_seconds)

    try:
        output, _ = process.communicate(timeout=BLUETOOTHCTL_CONNECT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        output, _ = process.communicate()
        raise SystemExit(_format_bluetoothctl_failure(output)) from exc

    return output


def _run_bluetoothctl_command(commands: tuple[str, ...], timeout: float) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl"],
            input="\n".join(commands) + "\n",
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"bluetoothctl command failed: {exc}") from exc

    if result.returncode != 0:
        raise SystemExit(_format_bluetoothctl_failure(result.stdout))

    return result.stdout


def _merge_device_info(device: BluetoothDevice, info: str) -> BluetoothDevice:
    fields: dict[str, str] = {}

    for raw_line in info.splitlines():
        line = _strip_bluetooth_prompt(raw_line.strip())
        match = INFO_FIELD_RE.match(line)
        if match:
            fields[match.group("field")] = match.group("value").strip()

    name = fields.get("Name") or device.name
    alias = fields.get("Alias") or name or device.alias

    return BluetoothDevice(
        address=device.address,
        name=name or "unknown",
        alias=alias or "unknown",
        paired=_parse_bool(fields.get("Paired")),
        connected=_parse_bool(fields.get("Connected")),
        rssi=_parse_int(fields.get("RSSI")),
    )


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None

    lowered = value.casefold()
    if lowered == "yes":
        return True
    if lowered == "no":
        return False

    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def _strip_bluetooth_prompt(line: str) -> str:
    if "]#" not in line:
        return line

    return line.split("]#", 1)[1].strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List nearby Bluetooth devices using BlueZ bluetoothctl.")
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_SCAN_SECONDS,
        help="number of seconds to scan before printing devices",
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "bredr", "le"),
        default=DEFAULT_SCAN_TRANSPORT,
        help="Bluetooth scan transport; use bredr for many classic speakers",
    )
    parser.add_argument("--name", default="", help="only print devices whose name or alias contains this text")
    parser.add_argument("--named-only", action="store_true", help="hide unresolved address-like Bluetooth entries")
    parser.add_argument(
        "--connect",
        action="store_true",
        help="pair/trust/connect the named device instead of only listing devices",
    )
    parser.add_argument("--json", action="store_true", help="print devices as JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.connect:
        if not args.name:
            raise SystemExit("--connect requires --name")

        result = ensure_device_connected(
            name=args.name,
            scan_seconds=args.seconds,
            transport=args.transport,
        )
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        else:
            print(result.message)
        raise SystemExit(0 if result.connected else 1)

    devices = list_nearby_devices(
        scan_seconds=args.seconds,
        transport=args.transport,
        name_filter=args.name,
        named_only=args.named_only,
    )

    if args.json:
        print(json.dumps([asdict(device) for device in devices], indent=2))
        return

    if not devices:
        print("No Bluetooth devices found.")
        return

    for device in devices:
        details = []
        if device.alias and device.alias != device.name:
            details.append(f"alias={device.alias}")
        if device.rssi is not None:
            details.append(f"rssi={device.rssi}")
        if device.paired is True:
            details.append("paired")
        if device.connected is True:
            details.append("connected")

        suffix = f"  ({', '.join(details)})" if details else ""
        print(f"{device.address}  {device.name}{suffix}")


if __name__ == "__main__":
    main()
