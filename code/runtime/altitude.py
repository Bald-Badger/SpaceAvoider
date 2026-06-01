"""Altitude calculations and calibration helpers."""

from __future__ import annotations

from dataclasses import dataclass

from code.helper.pressure_helper import PASCALS_PER_INHG
from code.runtime.models import GpsSample, PressureAverages, StateSnapshot


STANDARD_ALTIMETER_INHG = 29.92
PRESSURE_ALTITUDE_EXPONENT = 0.190284
PRESSURE_ALTITUDE_SCALE_FT = 145366.45


@dataclass(frozen=True)
class CalibrationInput:
    known_altitude_ft: float
    altimeter_setting_inhg: float
    pressure: PressureAverages
    humidity_percent: float | None
    gps: GpsSample | None
    gps_sanity_threshold_ft: float


@dataclass(frozen=True)
class CalibrationResult:
    altimeter_setting_inhg: float
    calibration_offset_ft: float
    raw_altitude_ft: float
    calibrated_altitude_ft: float
    gps_delta_ft: float | None
    gps_sanity_failed: bool
    notes: tuple[str, ...]


class ScalarKalmanFilter:
    """Simple one-input smoother for pressure-derived altitude."""

    def __init__(self, process_variance: float = 4.0, measurement_variance: float = 100.0) -> None:
        self.process_variance = process_variance
        self.measurement_variance = measurement_variance
        self.estimate: float | None = None
        self.error_covariance = 1.0
        self.last_timestamp: float | None = None

    def reset(self, estimate: float | None = None, timestamp: float | None = None) -> None:
        self.estimate = estimate
        self.error_covariance = 1.0
        self.last_timestamp = timestamp

    def update(self, measurement: float, timestamp: float) -> tuple[float, float]:
        if self.estimate is None:
            self.estimate = measurement
            self.last_timestamp = timestamp
            return measurement, 0.0

        previous_estimate = self.estimate
        previous_timestamp = self.last_timestamp or timestamp
        dt = max(0.001, timestamp - previous_timestamp)

        self.error_covariance += self.process_variance * dt
        kalman_gain = self.error_covariance / (self.error_covariance + self.measurement_variance)
        self.estimate = self.estimate + kalman_gain * (measurement - self.estimate)
        self.error_covariance = (1.0 - kalman_gain) * self.error_covariance
        self.last_timestamp = timestamp

        vertical_speed_fpm = (self.estimate - previous_estimate) / dt * 60.0
        return self.estimate, vertical_speed_fpm


def pressure_to_altitude_ft(
    pressure_pa: float,
    altimeter_setting_inhg: float = STANDARD_ALTIMETER_INHG,
) -> float:
    pressure_inhg = pressure_pa / PASCALS_PER_INHG
    return PRESSURE_ALTITUDE_SCALE_FT * (
        1.0 - (pressure_inhg / altimeter_setting_inhg) ** PRESSURE_ALTITUDE_EXPONENT
    )


def baro_altitude_from_snapshot(snapshot: StateSnapshot) -> float | None:
    if snapshot.pressure_average is None:
        return None

    raw_altitude_ft = pressure_to_altitude_ft(
        snapshot.pressure_average.pressure_pa,
        snapshot.altimeter_setting_inhg,
    )
    return raw_altitude_ft + snapshot.calibration_offset_ft


def calibrate_altitude(calibration_input: CalibrationInput) -> CalibrationResult:
    raw_altitude_ft = pressure_to_altitude_ft(
        calibration_input.pressure.pressure_pa,
        calibration_input.altimeter_setting_inhg,
    )
    calibration_offset_ft = calibration_input.known_altitude_ft - raw_altitude_ft
    calibrated_altitude_ft = raw_altitude_ft + calibration_offset_ft
    gps_delta_ft = None
    gps_sanity_failed = False
    notes: list[str] = []

    if calibration_input.humidity_percent is None:
        notes.append("humidity unavailable; humidity correction placeholder skipped")
    else:
        notes.append("humidity captured for future correction model")

    if calibration_input.gps and calibration_input.gps.altitude_ft is not None:
        gps_delta_ft = calibrated_altitude_ft - calibration_input.gps.altitude_ft
        gps_sanity_failed = abs(gps_delta_ft) > calibration_input.gps_sanity_threshold_ft
    else:
        notes.append("GPS altitude unavailable; GPS sanity check placeholder skipped")

    return CalibrationResult(
        altimeter_setting_inhg=calibration_input.altimeter_setting_inhg,
        calibration_offset_ft=calibration_offset_ft,
        raw_altitude_ft=raw_altitude_ft,
        calibrated_altitude_ft=calibrated_altitude_ft,
        gps_delta_ft=gps_delta_ft,
        gps_sanity_failed=gps_sanity_failed,
        notes=tuple(notes),
    )
