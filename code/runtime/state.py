"""Thread-safe shared state for the app runtime."""

from __future__ import annotations

import threading
from collections import deque

from code.runtime.models import (
    AltitudeEstimate,
    GpsSample,
    HumiditySample,
    MetarSample,
    PressureAverages,
    PressureSample,
    StateSnapshot,
)


PRESSURE_BUFFER_SIZE = 8
DEFAULT_ALTIMETER_SETTING_INHG = 29.92
DEFAULT_KNOWN_ALTITUDE_FT = 1179.0


class RuntimeState:
    """Small lock-protected store shared by runtime workers."""

    def __init__(
        self,
        known_altitude_ft: float = DEFAULT_KNOWN_ALTITUDE_FT,
        altimeter_setting_inhg: float = DEFAULT_ALTIMETER_SETTING_INHG,
    ) -> None:
        self._lock = threading.RLock()
        self._humidity: HumiditySample | None = None
        self._pressure_samples: deque[PressureSample] = deque(maxlen=PRESSURE_BUFFER_SIZE)
        self._gps: GpsSample | None = None
        self._metar: MetarSample | None = None
        self._altitude: AltitudeEstimate | None = None
        self._altimeter_setting_inhg = altimeter_setting_inhg
        self._altimeter_version = 0
        self._calibration_offset_ft = 0.0
        self._known_altitude_ft = known_altitude_ft
        self._approach_mode = False

    def update_humidity(self, sample: HumiditySample) -> None:
        with self._lock:
            self._humidity = sample

    def add_pressure(self, sample: PressureSample) -> None:
        with self._lock:
            self._pressure_samples.append(sample)

    def update_gps(self, sample: GpsSample) -> None:
        with self._lock:
            self._gps = sample

    def update_metar(self, sample: MetarSample) -> None:
        with self._lock:
            self._metar = sample
            self._altimeter_setting_inhg = sample.altimeter_inhg
            self._altimeter_version += 1

    def update_altitude(self, estimate: AltitudeEstimate) -> None:
        with self._lock:
            self._altitude = estimate

    def update_calibration(self, altimeter_setting_inhg: float, calibration_offset_ft: float) -> None:
        with self._lock:
            self._altimeter_setting_inhg = altimeter_setting_inhg
            self._altimeter_version += 1
            self._calibration_offset_ft = calibration_offset_ft

    def set_approach_mode(self, active: bool) -> None:
        with self._lock:
            self._approach_mode = active

    def approach_mode_active(self) -> bool:
        with self._lock:
            return self._approach_mode

    def snapshot(self) -> StateSnapshot:
        with self._lock:
            pressure_samples = tuple(self._pressure_samples)
            return StateSnapshot(
                humidity=self._humidity,
                pressure_samples=pressure_samples,
                pressure_average=average_pressure_samples(pressure_samples),
                gps=self._gps,
                metar=self._metar,
                altitude=self._altitude,
                altimeter_setting_inhg=self._altimeter_setting_inhg,
                altimeter_version=self._altimeter_version,
                calibration_offset_ft=self._calibration_offset_ft,
                known_altitude_ft=self._known_altitude_ft,
                approach_mode=self._approach_mode,
            )


def average_pressure_samples(samples: tuple[PressureSample, ...]) -> PressureAverages | None:
    if not samples:
        return None

    count = len(samples)
    pressure_pa = sum(sample.pressure_pa for sample in samples) / count
    temperature_c = sum(sample.temperature_c for sample in samples) / count

    return PressureAverages(
        pressure_pa=pressure_pa,
        pressure_hpa=pressure_pa / 100.0,
        pressure_inhg=sum(sample.pressure_inhg for sample in samples) / count,
        temperature_c=temperature_c,
        sample_count=count,
    )
