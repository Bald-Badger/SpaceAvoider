"""Shared runtime data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HumiditySample:
    humidity_percent: float
    timestamp: float


@dataclass(frozen=True)
class PressureSample:
    pressure_pa: float
    pressure_hpa: float
    pressure_inhg: float
    temperature_c: float
    timestamp: float


@dataclass(frozen=True)
class PressureAverages:
    pressure_pa: float
    pressure_hpa: float
    pressure_inhg: float
    temperature_c: float
    sample_count: int


@dataclass(frozen=True)
class GpsSample:
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_ft: float | None
    has_fix: bool
    source: str | None
    timestamp: float


@dataclass(frozen=True)
class AltitudeEstimate:
    altitude_ft: float
    raw_baro_altitude_ft: float
    vertical_speed_fpm: float
    altimeter_setting_inhg: float
    calibration_offset_ft: float
    timestamp: float


@dataclass(frozen=True)
class MetarSample:
    station: str
    altimeter_inhg: float
    raw: str
    source: str
    timestamp: float


@dataclass(frozen=True)
class StateSnapshot:
    humidity: HumiditySample | None
    pressure_samples: tuple[PressureSample, ...]
    pressure_average: PressureAverages | None
    gps: GpsSample | None
    metar: MetarSample | None
    altitude: AltitudeEstimate | None
    altimeter_setting_inhg: float
    altimeter_version: int
    calibration_offset_ft: float
    known_altitude_ft: float
    approach_mode: bool
