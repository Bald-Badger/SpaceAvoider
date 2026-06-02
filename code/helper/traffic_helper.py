"""
Traffic helper functions for the SpaceAvoider toy avionics project.

Stratux exposes decoded ADS-B/TIS-B/other traffic through the /traffic
WebSocket. This helper normalizes the fields needed by a simple traffic display
and can optionally keep the raw Stratux update for debugging.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse


STRATUX_TRAFFIC_WS_URL = "ws://127.0.0.1/traffic"
METERS_PER_NM = 1852.0

TRAFFIC_SOURCE_NAMES = {
    1: "1090ES",
    2: "UAT",
    4: "OGN",
    5: "AIS",
}


def get_surrounding_traffic(
    traffic_ws_url: str = STRATUX_TRAFFIC_WS_URL,
    timeout_seconds: float = 3.0,
    listen_seconds: float = 5.0,
    max_age_seconds: float = 60.0,
    max_range_nm: float | None = None,
    ownship_altitude_ft: float | None = None,
    include_invalid_position: bool = False,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return nearby traffic from Stratux, sorted nearest first.

    Stratux sends one aircraft update per WebSocket message. This function
    listens briefly, keeps the newest update for each aircraft, filters stale
    targets, and returns a normalized list. Pass ``include_raw=True`` to keep
    the full Stratux update on each traffic item.
    """

    started_at = time.time()
    aircraft_by_key: dict[tuple[int | None, int | None], dict[str, Any]] = {}

    with _StratuxWebSocket(traffic_ws_url, timeout_seconds) as websocket:
        deadline = time.monotonic() + listen_seconds
        while time.monotonic() < deadline:
            remaining = max(0.1, min(timeout_seconds, deadline - time.monotonic()))
            message_text = websocket.receive_text(timeout_seconds=remaining)
            if message_text is None:
                continue

            update = _parse_traffic_message(message_text)
            if update is None:
                continue

            traffic = _normalize_traffic(update, ownship_altitude_ft, include_raw)
            if not include_invalid_position and not traffic["position_valid"]:
                continue
            if traffic["age_seconds"] is not None and traffic["age_seconds"] > max_age_seconds:
                continue
            if max_range_nm is not None and traffic["distance_nm"] is not None:
                if traffic["distance_nm"] > max_range_nm:
                    continue

            aircraft_by_key[(traffic["icao_int"], traffic["addr_type"])] = traffic

    traffic_list = sorted(
        aircraft_by_key.values(),
        key=lambda aircraft: (
            aircraft["distance_nm"] is None,
            aircraft["distance_nm"] if aircraft["distance_nm"] is not None else 999999.0,
            aircraft["age_seconds"] if aircraft["age_seconds"] is not None else 999999.0,
        ),
    )

    return {
        "available": bool(traffic_list),
        "source": traffic_ws_url,
        "read_at_unix": time.time(),
        "listen_started_unix": started_at,
        "listen_seconds": listen_seconds,
        "count": len(traffic_list),
        "traffic": traffic_list,
    }


def _normalize_traffic(
    update: dict[str, Any],
    ownship_altitude_ft: float | None,
    include_raw: bool,
) -> dict[str, Any]:
    icao_int = _to_int(update.get("Icao_addr"))
    addr_type = _to_int(update.get("Addr_type"))
    last_source = _to_int(update.get("Last_source"))
    distance_m = _to_float(update.get("Distance"))
    distance_estimated_m = _to_float(update.get("DistanceEstimated"))
    altitude_ft = _to_float(update.get("Alt"))

    relative_altitude_ft = None
    if ownship_altitude_ft is not None and altitude_ft is not None:
        relative_altitude_ft = altitude_ft - ownship_altitude_ft

    traffic = {
        "icao_int": icao_int,
        "icao_hex": _icao_hex(icao_int),
        "addr_type": addr_type,
        "target_type": update.get("TargetType"),
        "source": TRAFFIC_SOURCE_NAMES.get(last_source, f"unknown:{last_source}"),
        "last_source": last_source,
        "registration": _clean_text(update.get("Reg")),
        "callsign": _clean_text(update.get("Tail")),
        "squawk": update.get("Squawk"),
        "emitter_category": update.get("Emitter_category"),
        "on_ground": bool(update.get("OnGround")),
        "position_valid": bool(update.get("Position_valid")),
        "bearing_distance_valid": bool(update.get("BearingDist_valid")),
        "latitude_deg": _to_float(update.get("Lat")),
        "longitude_deg": _to_float(update.get("Lng")),
        "altitude_ft": altitude_ft,
        "altitude_is_gnss": bool(update.get("AltIsGNSS")),
        "relative_altitude_ft": relative_altitude_ft,
        "speed_kt": _to_float(update.get("Speed")) if update.get("Speed_valid") else None,
        "speed_valid": bool(update.get("Speed_valid")),
        "track_deg": _to_float(update.get("Track")) if update.get("Speed_valid") else None,
        "vertical_speed_fpm": _to_float(update.get("Vvel")),
        "bearing_deg_true": _to_float(update.get("Bearing")),
        "distance_m": distance_m,
        "distance_nm": distance_m / METERS_PER_NM if distance_m is not None else None,
        "distance_estimated_m": distance_estimated_m,
        "distance_estimated_nm": (
            distance_estimated_m / METERS_PER_NM
            if distance_estimated_m is not None
            else None
        ),
        "signal_dbfs": _to_float(update.get("SignalLevel")),
        "age_seconds": _to_float(update.get("Age")),
        "age_last_alt_seconds": _to_float(update.get("AgeLastAlt")),
        "timestamp": update.get("Timestamp"),
        "received_messages": update.get("ReceivedMsgs"),
        "extrapolated_position": bool(update.get("ExtrapolatedPosition")),
    }

    if include_raw:
        traffic["raw"] = update

    return traffic


def _parse_traffic_message(message_text: str) -> dict[str, Any] | None:
    try:
        message = json.loads(message_text)
    except json.JSONDecodeError:
        return None

    if isinstance(message, dict):
        return message
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _icao_hex(icao_int: int | None) -> str | None:
    if icao_int is None:
        return None
    return f"{icao_int:06X}"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _StratuxWebSocket:
    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.sock: socket.socket | None = None

    def __enter__(self) -> "_StratuxWebSocket":
        parsed = urlparse(self.url)
        if parsed.scheme != "ws":
            raise ValueError(f"Only ws:// URLs are supported: {self.url}")

        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        self.sock = socket.create_connection((host, port), timeout=self.timeout_seconds)
        self.sock.settimeout(self.timeout_seconds)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._read_http_response_header()
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise ConnectionError(f"Stratux WebSocket upgrade failed: {response!r}")

        return self

    def __exit__(self, *_exc: object) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def receive_text(self, timeout_seconds: float) -> str | None:
        if self.sock is None:
            raise RuntimeError("WebSocket is not connected.")

        self.sock.settimeout(timeout_seconds)
        try:
            first_two = self._read_exact(2)
        except socket.timeout:
            return None

        first_byte, second_byte = first_two
        opcode = first_byte & 0x0F
        masked = bool(second_byte & 0x80)
        payload_length = second_byte & 0x7F

        if payload_length == 126:
            payload_length = int.from_bytes(self._read_exact(2), "big")
        elif payload_length == 127:
            payload_length = int.from_bytes(self._read_exact(8), "big")

        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(payload_length) if payload_length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        if opcode == 0x8:
            return None
        if opcode in (0x9, 0xA):
            return None

        return None

    def _read_http_response_header(self) -> str:
        chunks = []
        while True:
            chunk = self._read_exact(1)
            chunks.append(chunk)
            response = b"".join(chunks)
            if b"\r\n\r\n" in response:
                return response.decode("iso-8859-1", errors="replace")

    def _read_exact(self, byte_count: int) -> bytes:
        if self.sock is None:
            raise RuntimeError("WebSocket is not connected.")

        data = bytearray()
        while len(data) < byte_count:
            chunk = self.sock.recv(byte_count - len(data))
            if not chunk:
                raise ConnectionError("WebSocket closed while reading.")
            data.extend(chunk)
        return bytes(data)


if __name__ == "__main__":
    print(json.dumps(get_surrounding_traffic(), indent=2, sort_keys=True))
