"""Small Stratux HTTP diagnostics helpers."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen


STRATUX_DEFAULT_BASE_URL = "http://127.0.0.1"


def get_json_endpoint(
    endpoint: str,
    base_url: str = STRATUX_DEFAULT_BASE_URL,
    timeout_seconds: float = 2.0,
) -> dict[str, Any] | list[Any]:
    """Read one Stratux JSON endpoint."""

    endpoint = endpoint.lstrip("/")
    url = f"{base_url.rstrip('/')}/{endpoint}"
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        raise ConnectionError(f"Could not read Stratux {endpoint} from {url}: HTTP {error.code}") from error
    except URLError as error:
        raise ConnectionError(f"Could not read Stratux {endpoint} from {url}") from error

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"Stratux {endpoint} did not return JSON: {payload[:200]!r}") from error

    if not isinstance(data, (dict, list)):
        raise ValueError(f"Stratux {endpoint} returned {type(data).__name__}, expected object/list")
    return data


def get_status(base_url: str = STRATUX_DEFAULT_BASE_URL, timeout_seconds: float = 2.0) -> dict[str, Any]:
    data = get_json_endpoint("getStatus", base_url=base_url, timeout_seconds=timeout_seconds)
    if not isinstance(data, dict):
        raise ValueError("Stratux getStatus did not return a JSON object")
    return data


def get_situation(base_url: str = STRATUX_DEFAULT_BASE_URL, timeout_seconds: float = 2.0) -> dict[str, Any]:
    data = get_json_endpoint("getSituation", base_url=base_url, timeout_seconds=timeout_seconds)
    if not isinstance(data, dict):
        raise ValueError("Stratux getSituation did not return a JSON object")
    return data


def get_towers(base_url: str = STRATUX_DEFAULT_BASE_URL, timeout_seconds: float = 2.0) -> dict[str, Any] | list[Any]:
    return get_json_endpoint("getTowers", base_url=base_url, timeout_seconds=timeout_seconds)


def now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

