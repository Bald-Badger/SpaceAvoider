"""
METAR helper functions for the SpaceAvoider toy avionics project.

Stratux exposes FIS-B weather through the /weather WebSocket. This module uses
only the Python standard library so it can run on a tight Stratux install.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.request import Request
from urllib.request import urlopen


STRATUX_WEATHER_WS_URL = "ws://127.0.0.1/weather"
AVIATIONWEATHER_METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_TYPES = {"METAR", "SPECI"}


def get_current_metar(
    station: str | None = None,
    weather_ws_url: str = STRATUX_WEATHER_WS_URL,
    timeout_seconds: float = 3.0,
    listen_seconds: float = 10.0,
) -> dict[str, Any]:
    """Return the newest METAR/SPECI received from Stratux weather.

    If ``station`` is provided, only that station is returned. If no matching
    METAR arrives during ``listen_seconds``, the result is a structured
    ``available: False`` response instead of an exception.
    """

    station = station.upper() if station else None
    metars = get_current_metars(
        station=station,
        weather_ws_url=weather_ws_url,
        timeout_seconds=timeout_seconds,
        listen_seconds=listen_seconds,
    )

    if metars:
        return {
            "available": True,
            "source": weather_ws_url,
            "read_at_unix": time.time(),
            "station": station,
            "metar": metars[0],
        }

    return {
        "available": False,
        "source": weather_ws_url,
        "read_at_unix": time.time(),
        "station": station,
        "reason": "No METAR/SPECI message received during listen window.",
    }


def get_current_metars(
    station: str | None = None,
    weather_ws_url: str = STRATUX_WEATHER_WS_URL,
    timeout_seconds: float = 3.0,
    listen_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Collect METAR/SPECI messages from Stratux weather for a short window."""

    station = station.upper() if station else None
    deadline = time.monotonic() + listen_seconds
    metars_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    with _WeatherWebSocket(weather_ws_url, timeout_seconds) as websocket:
        while time.monotonic() < deadline:
            remaining = max(0.1, min(timeout_seconds, deadline - time.monotonic()))
            message_text = websocket.receive_text(timeout_seconds=remaining)
            if message_text is None:
                continue

            message = _parse_weather_message(message_text)
            if message is None:
                continue

            message_type = str(message.get("Type", "")).upper()
            location = str(message.get("Location", "")).upper()
            if message_type not in METAR_TYPES:
                continue
            if station and location != station:
                continue

            metar = _normalize_metar_message(message)
            metars_by_key[(metar["station"], metar["type"])] = metar

    return sorted(
        metars_by_key.values(),
        key=lambda item: str(item.get("time_raw", "")),
        reverse=True,
    )


def parse_metar_text(raw_metar: str) -> dict[str, Any]:
    """Extract the small METAR pieces this project currently cares about."""

    altimeter_inhg = _parse_altimeter_inhg(raw_metar)
    return {
        "raw": raw_metar,
        "altimeter_inhg": altimeter_inhg,
        "flight_condition": _parse_flight_condition(raw_metar),
    }


def get_latest_metar_from_aviationweather(
    station: str,
    timeout_seconds: float = 5.0,
    hours: int = 2,
) -> dict[str, Any]:
    """Fetch the latest METAR for one station from AviationWeather.gov."""

    station = station.upper()
    query = urlencode(
        {
            "ids": station,
            "format": "json",
            "taf": "false",
            "hours": str(hours),
        }
    )
    url = f"{AVIATIONWEATHER_METAR_URL}?{query}"
    request = Request(url, headers={"User-Agent": "SpaceAvoider/0.1"})

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except URLError as error:
        raise ConnectionError(f"Could not read METAR from {url}") from error

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {
            "available": False,
            "source": url,
            "read_at_unix": time.time(),
            "station": station,
            "reason": "AviationWeather.gov did not return JSON.",
            "raw_response": payload[:500],
        }
    if not isinstance(data, list) or not data:
        return {
            "available": False,
            "source": url,
            "read_at_unix": time.time(),
            "station": station,
            "reason": "No METAR returned by AviationWeather.gov.",
        }

    message = data[0]
    if not isinstance(message, dict):
        raise ValueError(f"AviationWeather.gov METAR response was not a JSON object: {url}")

    raw_metar = _first_present(message, "rawOb", "raw_text", "raw", "metar")
    raw_metar = "" if raw_metar is None else str(raw_metar)
    parsed = parse_metar_text(raw_metar)
    altimeter_inhg = _to_float(_first_present(message, "altim", "altimeter_inhg"))
    if altimeter_inhg is not None and altimeter_inhg > 100.0:
        altimeter_inhg = round(altimeter_inhg * 0.0295299830714, 2)
    if altimeter_inhg is None:
        altimeter_inhg = parsed["altimeter_inhg"]

    return {
        "available": True,
        "source": url,
        "read_at_unix": time.time(),
        "station": str(_first_present(message, "icaoId", "station_id", default=station)).upper(),
        "raw": raw_metar,
        "altimeter_inhg": altimeter_inhg,
        "flight_condition": parsed["flight_condition"],
        "aviationweather": message,
    }


def _normalize_metar_message(message: dict[str, Any]) -> dict[str, Any]:
    raw_metar = str(message.get("Data", ""))
    parsed = parse_metar_text(raw_metar)
    return {
        "type": str(message.get("Type", "")).upper(),
        "station": str(message.get("Location", "")).upper(),
        "time_raw": message.get("Time"),
        "received_local": message.get("LocaltimeReceived"),
        "raw": raw_metar,
        "altimeter_inhg": parsed["altimeter_inhg"],
        "flight_condition": parsed["flight_condition"],
        "stratux": message,
    }


def _parse_weather_message(message_text: str) -> dict[str, Any] | None:
    try:
        message = json.loads(message_text)
    except json.JSONDecodeError:
        return None

    if isinstance(message, dict):
        return message
    return None


def _parse_altimeter_inhg(raw_metar: str) -> float | None:
    match = re.search(r"\bA(\d{4})\b", raw_metar)
    if match:
        return int(match.group(1)) / 100.0

    match = re.search(r"\bQ(\d{4})\b", raw_metar)
    if match:
        hpa = int(match.group(1))
        return round(hpa * 0.0295299830714, 2)

    return None


def _parse_flight_condition(raw_metar: str) -> str | None:
    visibility_sm = _parse_visibility_sm(raw_metar)
    ceiling_ft = _parse_ceiling_ft(raw_metar)

    if visibility_sm is None and ceiling_ft is None:
        return None

    visibility = visibility_sm if visibility_sm is not None else 99.0
    ceiling = ceiling_ft if ceiling_ft is not None else 99999

    if visibility > 5 and ceiling > 3000:
        return "VFR"
    if visibility >= 3 and ceiling >= 1000:
        return "MVFR"
    if visibility >= 1 and ceiling >= 500:
        return "IFR"
    return "LIFR"


def _parse_visibility_sm(raw_metar: str) -> float | None:
    match = re.search(r"\b(\d+)\s+(\d+)/(\d+)SM\b", raw_metar)
    if match:
        whole = int(match.group(1))
        numerator = int(match.group(2))
        denominator = int(match.group(3))
        return whole + numerator / denominator

    match = re.search(r"\b(\d+)/(\d+)SM\b", raw_metar)
    if match:
        numerator = int(match.group(1))
        denominator = int(match.group(2))
        return numerator / denominator

    match = re.search(r"\b(\d+)SM\b", raw_metar)
    if match:
        return float(match.group(1))

    return None


def _parse_ceiling_ft(raw_metar: str) -> int | None:
    ceilings = []
    for match in re.finditer(r"\b(?:BKN|OVC|VV)(\d{3})\b", raw_metar):
        ceilings.append(int(match.group(1)) * 100)

    if ceilings:
        return min(ceilings)
    return None


def _first_present(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _WeatherWebSocket:
    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.sock: socket.socket | None = None

    def __enter__(self) -> "_WeatherWebSocket":
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
            raise ConnectionError(f"Stratux weather WebSocket upgrade failed: {response!r}")

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
    print(json.dumps(get_current_metar(), indent=2, sort_keys=True))
