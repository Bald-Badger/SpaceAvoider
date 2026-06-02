"""
GPS helper functions for the SpaceAvoider toy avionics project.

The GPYes/Stratux GPS path is expected to be shared through gpsd. Reading gpsd
keeps this app from fighting Stratux for direct access to the USB serial GPS.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


GPSD_DEFAULT_HOST = "127.0.0.1"
GPSD_DEFAULT_PORT = 2947
STRATUX_DEFAULT_BASE_URL = "http://127.0.0.1"
MPS_TO_KT = 1.9438444924406
MPS_TO_MPH = 2.2369362920544
KT_TO_MPS = 0.51444444444444


def get_current_gps_readings(
    host: str = GPSD_DEFAULT_HOST,
    port: int = GPSD_DEFAULT_PORT,
    stratux_base_url: str | None = STRATUX_DEFAULT_BASE_URL,
    timeout_seconds: float = 3.0,
    min_collect_seconds: float = 1.0,
    include_raw_packets: bool = True,
) -> dict[str, Any]:
    """Return the latest available GPS readings.

    gpsd is tried first because it exposes the richest GPS receiver details.
    If gpsd is unavailable and ``stratux_base_url`` is not ``None``, this falls
    back to Stratux's ``/getSituation`` endpoint.

    The returned gpsd dictionary has two layers:
    - ``fix``: normalized fields that are convenient for app logic.
    - ``gpsd``: the latest raw packet of each useful gpsd class.

    gpsd sends data as newline-delimited JSON. We enable WATCH mode, collect
    packets for a short window, and return the newest TPV/SKY/GST/DEVICE data
    seen in that window.
    """

    try:
        return get_current_gps_readings_from_gpsd(
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
            min_collect_seconds=min_collect_seconds,
            include_raw_packets=include_raw_packets,
        )
    except OSError as gpsd_error:
        if stratux_base_url is None:
            raise

        stratux_readings = get_current_gps_readings_from_stratux(
            base_url=stratux_base_url,
            timeout_seconds=timeout_seconds,
        )
        stratux_readings["gpsd_error"] = str(gpsd_error)
        return stratux_readings


def get_current_heading_and_speed(
    host: str = GPSD_DEFAULT_HOST,
    port: int = GPSD_DEFAULT_PORT,
    stratux_base_url: str | None = STRATUX_DEFAULT_BASE_URL,
    timeout_seconds: float = 3.0,
    readings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return current GPS ground track and ground speed.

    GPS does not know aircraft nose heading. The ``heading_deg`` field below is
    an alias for GPS track/course over ground so display code can stay simple.
    """

    if readings is None:
        readings = get_current_gps_readings(
            host=host,
            port=port,
            stratux_base_url=stratux_base_url,
            timeout_seconds=timeout_seconds,
            include_raw_packets=False,
        )

    fix = readings.get("fix", {})
    track_deg = _normalize_degrees(_to_float(fix.get("track_deg")))
    speed_mps = _to_float(fix.get("ground_speed_mps"))
    speed_kt = None
    speed_mph = None
    speed_source = None

    if speed_mps is not None:
        speed_kt = speed_mps * MPS_TO_KT
        speed_mph = speed_mps * MPS_TO_MPH
        speed_source = "gpsd_mps"
    else:
        stratux_speed = _to_float(fix.get("ground_speed_stratux"))
        if stratux_speed is not None:
            speed_kt = stratux_speed
            speed_mps = stratux_speed * KT_TO_MPS
            speed_mph = speed_mps * MPS_TO_MPH
            speed_source = "stratux_knots"

    return {
        "source": readings.get("source"),
        "read_at_unix": readings.get("read_at_unix"),
        "has_2d_fix": bool(fix.get("has_2d_fix")),
        "has_3d_fix": bool(fix.get("has_3d_fix")),
        "time_utc": fix.get("time_utc"),
        "heading_deg": track_deg,
        "track_deg": track_deg,
        "heading_is_gps_track": True,
        "speed_mps": speed_mps,
        "speed_kt": speed_kt,
        "speed_mph": speed_mph,
        "speed_source": speed_source,
        "note": "GPS heading is track/course over ground, not aircraft nose heading.",
    }


def get_current_gps_readings_from_gpsd(
    host: str = GPSD_DEFAULT_HOST,
    port: int = GPSD_DEFAULT_PORT,
    timeout_seconds: float = 3.0,
    min_collect_seconds: float = 1.0,
    include_raw_packets: bool = True,
) -> dict[str, Any]:
    """Return the latest available GPS readings from gpsd."""

    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    latest_by_class: dict[str, dict[str, Any]] = {}
    raw_packets: list[dict[str, Any]] = []

    with socket.create_connection((host, port), timeout=timeout_seconds) as gpsd:
        gpsd.settimeout(0.5)
        gpsd.sendall(b'?WATCH={"enable":true,"json":true};\n')

        buffer = ""
        while time.monotonic() < deadline:
            try:
                chunk = gpsd.recv(4096)
            except socket.timeout:
                if time.monotonic() - started_at >= min_collect_seconds:
                    break
                continue

            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                packet = _parse_gpsd_packet(line)
                if packet is None:
                    continue

                packet_class = packet.get("class")
                if isinstance(packet_class, str):
                    latest_by_class[packet_class] = packet
                if include_raw_packets:
                    raw_packets.append(packet)

            if (
                time.monotonic() - started_at >= min_collect_seconds
                and "TPV" in latest_by_class
                and "SKY" in latest_by_class
            ):
                break

    result = {
        "source": f"gpsd://{host}:{port}",
        "read_at_unix": time.time(),
        "fix": _normalize_fix(latest_by_class),
        "gpsd": latest_by_class,
    }

    if include_raw_packets:
        result["raw_packets"] = raw_packets

    return result


def get_current_gps_readings_from_stratux(
    base_url: str = STRATUX_DEFAULT_BASE_URL,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Return GPS-related readings from Stratux's /getSituation JSON endpoint."""

    url = f"{base_url.rstrip('/')}/getSituation"
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except URLError as error:
        raise ConnectionError(f"Could not read Stratux situation from {url}") from error

    situation = json.loads(payload)
    if not isinstance(situation, dict):
        raise ValueError(f"Stratux situation response was not a JSON object: {url}")

    return {
        "source": url,
        "read_at_unix": time.time(),
        "fix": _normalize_stratux_situation(situation),
        "stratux": situation,
    }


def _parse_gpsd_packet(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None

    try:
        packet = json.loads(line)
    except json.JSONDecodeError:
        return None

    if isinstance(packet, dict):
        return packet
    return None


def _normalize_fix(latest_by_class: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tpv = latest_by_class.get("TPV", {})
    sky = latest_by_class.get("SKY", {})
    gst = latest_by_class.get("GST", {})
    device = latest_by_class.get("DEVICE", {})

    satellites = sky.get("satellites")
    satellites_used = None
    satellites_visible = None
    if isinstance(satellites, list):
        satellites_visible = len(satellites)
        satellites_used = sum(1 for satellite in satellites if satellite.get("used"))

    return {
        "mode": tpv.get("mode"),
        "mode_name": _mode_name(tpv.get("mode")),
        "has_2d_fix": tpv.get("mode") in (2, 3),
        "has_3d_fix": tpv.get("mode") == 3,
        "time_utc": tpv.get("time"),
        "latitude_deg": tpv.get("lat"),
        "longitude_deg": tpv.get("lon"),
        "altitude_m": tpv.get("altMSL", tpv.get("altHAE", tpv.get("alt"))),
        "altitude_msl_m": tpv.get("altMSL"),
        "altitude_hae_m": tpv.get("altHAE"),
        "ground_speed_mps": tpv.get("speed"),
        "track_deg": tpv.get("track"),
        "climb_mps": tpv.get("climb"),
        "magnetic_variation_deg": tpv.get("magvar"),
        "epx_m": tpv.get("epx"),
        "epy_m": tpv.get("epy"),
        "epv_m": tpv.get("epv"),
        "eps_mps": tpv.get("eps"),
        "epc_mps": tpv.get("epc"),
        "ept_seconds": tpv.get("ept"),
        "horizontal_dop": sky.get("hdop"),
        "vertical_dop": sky.get("vdop"),
        "position_dop": sky.get("pdop"),
        "satellites_used": satellites_used,
        "satellites_visible": satellites_visible,
        "satellites": satellites,
        "gst": gst or None,
        "device_path": device.get("path"),
        "device_driver": device.get("driver"),
        "device_subtype": device.get("subtype"),
    }


def _normalize_stratux_situation(situation: dict[str, Any]) -> dict[str, Any]:
    latitude = _first_present(
        situation,
        "GPSLatitude",
        "Lat",
        "Latitude",
        "GPSLat",
    )
    longitude = _first_present(
        situation,
        "GPSLongitude",
        "Lng",
        "Lon",
        "Longitude",
        "GPSLon",
    )
    altitude_m = _first_present(
        situation,
        "GPSAltitudeMSL",
        "GPSAltitude",
        "GPSHeightMSL",
        "GPSHeightAboveEllipsoid",
        "Alt",
    )
    speed = _first_present(
        situation,
        "GPSGroundSpeed",
        "GroundSpeed",
        "Speed",
    )
    track = _first_present(
        situation,
        "GPSTrueCourse",
        "GPSTrack",
        "Track",
        "Course",
    )
    climb = _first_present(
        situation,
        "GPSVerticalSpeed",
        "VerticalSpeed",
        "Climb",
    )
    satellites = _first_present(
        situation,
        "GPSSatellites",
        "GPSSats",
        "Satellites",
        "Sats",
    )
    satellites_seen = _first_present(
        situation,
        "GPSSatellitesSeen",
        "SatellitesSeen",
    )
    satellites_tracked = _first_present(
        situation,
        "GPSSatellitesTracked",
        "SatellitesTracked",
    )
    fix_quality = _first_present(
        situation,
        "GPSFixQuality",
        "GPSFix",
        "FixQuality",
    )
    has_stratux_fix = _stratux_fix_quality_has_fix(fix_quality)
    has_2d_fix = has_stratux_fix and latitude is not None and longitude is not None

    return {
        "mode": None,
        "mode_name": "stratux",
        "has_2d_fix": has_2d_fix,
        "has_3d_fix": has_2d_fix and altitude_m is not None,
        "time_utc": _first_present(
            situation,
            "GPSTime",
            "GPSLastFixLocalTimeStr",
            "GPSLastFixSinceMidnightUTC",
        ),
        "latitude_deg": latitude,
        "longitude_deg": longitude,
        "altitude_m": altitude_m,
        "altitude_msl_m": _first_present(
            situation,
            "GPSAltitudeMSL",
            "GPSHeightMSL",
        ),
        "altitude_hae_m": _first_present(
            situation,
            "GPSHeightAboveEllipsoid",
            "GPSAltitudeHAE",
        ),
        "ground_speed_mps": None,
        "ground_speed_stratux": speed,
        "track_deg": track,
        "climb_mps": None,
        "climb_stratux": climb,
        "magnetic_variation_deg": None,
        "epx_m": _first_present(
            situation,
            "GPSHorizontalAccuracy",
            "GPSHorizontalProtectionLevel",
        ),
        "epy_m": _first_present(
            situation,
            "GPSHorizontalAccuracy",
            "GPSHorizontalProtectionLevel",
        ),
        "epv_m": _first_present(
            situation,
            "GPSVerticalAccuracy",
            "GPSVerticalProtectionLevel",
        ),
        "eps_mps": None,
        "epc_mps": None,
        "ept_seconds": None,
        "horizontal_dop": _first_present(situation, "GPSHDOP", "HDOP"),
        "vertical_dop": _first_present(situation, "GPSVDOP", "VDOP"),
        "position_dop": _first_present(situation, "GPSPDOP", "PDOP"),
        "satellites_used": _first_present(situation, "GPSSatellites", satellites_tracked),
        "satellites_visible": satellites_seen if satellites_seen is not None else satellites,
        "satellites_tracked": satellites_tracked,
        "satellites": None,
        "gst": None,
        "device_path": None,
        "device_driver": "stratux",
        "device_subtype": fix_quality,
    }


def _stratux_fix_quality_has_fix(fix_quality: Any) -> bool:
    if fix_quality is None:
        return True

    if isinstance(fix_quality, str):
        try:
            fix_quality = int(fix_quality)
        except ValueError:
            return fix_quality.strip().lower() not in {"", "0", "none", "no_fix"}

    return bool(fix_quality)


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _normalize_degrees(value: float | None) -> float | None:
    if value is None:
        return None
    return value % 360.0


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mode_name(mode: Any) -> str:
    return {
        0: "unknown",
        1: "no_fix",
        2: "2d_fix",
        3: "3d_fix",
    }.get(mode, "unknown")


if __name__ == "__main__":
    print(json.dumps(get_current_gps_readings(), indent=2, sort_keys=True))
