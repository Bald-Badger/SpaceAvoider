"""Runtime worker threads."""

from __future__ import annotations

import queue
import threading
import time
import json
from collections.abc import Callable
from dataclasses import dataclass
from math import atan2
from math import cos
from math import radians
from math import sin
from math import sqrt

from code.helper import gps_helper, humidity_helper, keypad_helper, metar_helper, pressure_helper, stratux_helper
from code.runtime.altitude import ScalarKalmanFilter, pressure_to_altitude_ft
from code.runtime.events import KeyPressedEvent, RuntimeEvent, SensorFaultEvent
from code.runtime.models import AltitudeEstimate, GpsSample, HumiditySample, MetarSample, PressureSample
from code.runtime.state import RuntimeState


HUMIDITY_PERIOD_SECONDS = 10.0
PRESSURE_PERIOD_SECONDS = 0.5
APPROACH_PRESSURE_PERIOD_SECONDS = 0.1
GPS_PERIOD_SECONDS = 1.0
METAR_PERIOD_SECONDS = 300.0
ALTITUDE_PERIOD_SECONDS = 0.5
KEYPAD_POLL_INTERVAL_SECONDS = 0.03
KEYPAD_DEBOUNCE_SECONDS = 0.05
FAULT_THROTTLE_SECONDS = 5.0
DIAGNOSTIC_STATUS_PERIOD_SECONDS = 5.0
DIAGNOSTIC_TOWER_PERIOD_SECONDS = 30.0
DIAGNOSTIC_WEATHER_TIMEOUT_LOG_SECONDS = 30.0
M_TO_FT = 3.280839895
EARTH_RADIUS_NM = 3440.065


@dataclass(frozen=True)
class MetarStation:
    station: str
    latitude_deg: float
    longitude_deg: float


METAR_STATIONS = (
    MetarStation("KCHD", 33.2691, -111.8111),
    MetarStation("A39", 32.9917, -111.9186),
)


def start_runtime_workers(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
    enable_humidity: bool = True,
    enable_pressure: bool = True,
    enable_gps: bool = True,
    enable_metar: bool = True,
    enable_keypad: bool = True,
    enable_diagnostics: bool = True,
) -> list[threading.Thread]:
    worker_specs: list[tuple[str, Callable[[], None]]] = []

    if enable_humidity:
        worker_specs.append(("humidity", lambda: humidity_worker(state, events, stop_event)))
    if enable_pressure:
        worker_specs.append(("pressure", lambda: pressure_worker(state, events, stop_event)))
    if enable_gps:
        worker_specs.append(("gps", lambda: gps_worker(state, events, stop_event)))
    if enable_metar:
        worker_specs.append(("metar", lambda: metar_worker(state, events, stop_event)))
    if enable_keypad:
        worker_specs.append(("keypad", lambda: keypad_worker(events, stop_event)))
    if enable_diagnostics:
        worker_specs.append(("diagnostics", lambda: diagnostics_worker(events, stop_event)))

    worker_specs.append(("altitude", lambda: altitude_worker(state, events, stop_event)))

    threads = [
        threading.Thread(target=target, name=f"spaceavoider-{name}", daemon=True)
        for name, target in worker_specs
    ]
    for thread in threads:
        thread.start()
    return threads


def humidity_worker(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    dht = None
    last_fault_at = 0.0

    try:
        while not stop_event.is_set():
            if dht is None:
                try:
                    dht = humidity_helper.create_dht11()
                except (Exception, SystemExit) as exc:
                    last_fault_at = _emit_fault(events, "humidity", exc, last_fault_at)
                    _wait(stop_event, HUMIDITY_PERIOD_SECONDS)
                    continue

            try:
                reading = humidity_helper.read_humidity_temperature_from_sensor(dht)
                state.update_humidity(
                    HumiditySample(humidity_percent=reading.humidity_percent, timestamp=time.time())
                )
                print(
                    f"[humidity-debug] humidity={reading.humidity_percent:.1f}% temp={reading.temperature_c:.1f} C",
                    flush=True,
                )
            except (Exception, SystemExit) as exc:
                last_fault_at = _emit_fault(events, "humidity", exc, last_fault_at)

            _wait(stop_event, HUMIDITY_PERIOD_SECONDS)
    finally:
        if dht is not None:
            dht.exit()


def pressure_worker(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    sensor = None
    last_fault_at = 0.0

    while not stop_event.is_set():
        if sensor is None:
            try:
                sensor = pressure_helper.open_pressure_sensor()
            except (Exception, SystemExit) as exc:
                last_fault_at = _emit_fault(events, "pressure", exc, last_fault_at)
                _wait(stop_event, 2.0)
                continue

        try:
            reading = pressure_helper.read_pressure_from_sensor(sensor)
            state.add_pressure(
                PressureSample(
                    pressure_pa=reading.pressure_pa,
                    pressure_hpa=reading.pressure_hpa,
                    pressure_inhg=reading.pressure_inhg,
                    temperature_c=reading.temperature_c,
                    timestamp=time.time(),
                )
            )
            print(
                "[pressure-debug] "
                f"pressure={reading.pressure_hpa:.2f} hPa "
                f"pressure_pa={reading.pressure_pa:.2f} "
                f"pressure_inhg={reading.pressure_inhg:.4f} "
                f"temp={reading.temperature_c:.2f} C "
                f"approach={state.approach_mode_active()}",
                flush=True,
            )
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "pressure", exc, last_fault_at)
            sensor = None

        period = APPROACH_PRESSURE_PERIOD_SECONDS if state.approach_mode_active() else PRESSURE_PERIOD_SECONDS
        _wait(stop_event, period)


def gps_worker(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    last_fault_at = 0.0

    while not stop_event.is_set():
        try:
            readings = gps_helper.get_current_gps_readings(
                timeout_seconds=0.8,
                min_collect_seconds=0.2,
                include_raw_packets=False,
            )
            fix = readings.get("fix", {})
            altitude_m = _to_float(fix.get("altitude_m"))
            print(_format_gps_debug(readings, fix), flush=True)
            state.update_gps(
                GpsSample(
                    latitude_deg=_to_float(fix.get("latitude_deg")),
                    longitude_deg=_to_float(fix.get("longitude_deg")),
                    altitude_ft=None if altitude_m is None else altitude_m * M_TO_FT,
                    has_fix=bool(fix.get("has_2d_fix") or fix.get("has_3d_fix")),
                    source=readings.get("source"),
                    timestamp=time.time(),
                )
            )
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "gps", exc, last_fault_at)

        _wait(stop_event, GPS_PERIOD_SECONDS)


def metar_worker(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    last_fault_at = 0.0

    while not stop_event.is_set():
        try:
            update_metar_altimeter(state)
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "metar", exc, last_fault_at)

        _wait(stop_event, METAR_PERIOD_SECONDS)


def update_metar_altimeter(state: RuntimeState) -> MetarSample:
    stations = metar_station_candidates(state.snapshot().gps)
    print(
        "[metar-debug] candidates="
        + ",".join(station.station for station in stations)
        + " source=aviationweather",
        flush=True,
    )
    metar = get_first_available_metar(stations)
    altimeter_inhg = _to_float(metar.get("altimeter_inhg"))
    if not metar.get("available") or altimeter_inhg is None:
        raise RuntimeError(f"METAR altimeter unavailable for {stations[0].station}")

    sample = MetarSample(
        station=str(metar.get("station") or stations[0].station).upper(),
        altimeter_inhg=altimeter_inhg,
        raw=str(metar.get("raw") or ""),
        source=str(metar.get("source") or ""),
        timestamp=time.time(),
    )
    state.update_metar(sample)
    print(
        f"[metar] station={sample.station} altimeter={sample.altimeter_inhg:.2f} inHg raw={sample.raw!r}",
        flush=True,
    )
    return sample


def choose_metar_station(gps: GpsSample | None) -> MetarStation:
    # TODO: Expand METAR selection to any possible METAR station in the USA.
    if gps is None or not gps.has_fix or gps.latitude_deg is None or gps.longitude_deg is None:
        return METAR_STATIONS[0]

    return min(
        METAR_STATIONS,
        key=lambda station: distance_nm(
            gps.latitude_deg,
            gps.longitude_deg,
            station.latitude_deg,
            station.longitude_deg,
        ),
    )


def metar_station_candidates(gps: GpsSample | None) -> tuple[MetarStation, ...]:
    preferred = choose_metar_station(gps)
    return (preferred,) + tuple(station for station in METAR_STATIONS if station != preferred)


def get_first_available_metar(stations: tuple[MetarStation, ...]) -> dict:
    last_result = None
    for station in stations:
        print(f"[metar-debug] fetch station={station.station}", flush=True)
        result = metar_helper.get_latest_metar_from_aviationweather(station.station)
        last_result = result
        print(
            "[metar-debug] result "
            f"station={station.station} available={result.get('available')} "
            f"altimeter={result.get('altimeter_inhg')} reason={result.get('reason')} "
            f"source={result.get('source')}",
            flush=True,
        )
        if result.get("available") and _to_float(result.get("altimeter_inhg")) is not None:
            return result

    if last_result is not None:
        return last_result
    return {"available": False, "reason": "No METAR stations configured."}


def distance_nm(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    lat1 = radians(lat1_deg)
    lat2 = radians(lat2_deg)
    delta_lat = radians(lat2_deg - lat1_deg)
    delta_lon = radians(lon2_deg - lon1_deg)

    a = sin(delta_lat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2.0) ** 2
    c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))
    return EARTH_RADIUS_NM * c


def keypad_worker(
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    last_fault_at = 0.0

    while not stop_event.is_set():
        try:
            with keypad_helper.open_keypad() as keypad:
                for press in keypad_helper.iter_key_presses(
                    keypad,
                    poll_interval=KEYPAD_POLL_INTERVAL_SECONDS,
                    debounce_seconds=KEYPAD_DEBOUNCE_SECONDS,
                    stop_event=stop_event,
                ):
                    events.put(KeyPressedEvent(key=press.key, timestamp=press.timestamp))
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "keypad", exc, last_fault_at)
            _wait(stop_event, 2.0)


def altitude_worker(
    state: RuntimeState,
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    kalman = ScalarKalmanFilter()
    last_fault_at = 0.0
    last_altimeter_version: int | None = None

    while not stop_event.is_set():
        try:
            snapshot = state.snapshot()
            if snapshot.pressure_average is not None:
                timestamp = time.time()
                raw_altitude_ft = pressure_to_altitude_ft(
                    snapshot.pressure_average.pressure_pa,
                    snapshot.altimeter_setting_inhg,
                )
                measurement_ft = raw_altitude_ft + snapshot.calibration_offset_ft
                if last_altimeter_version != snapshot.altimeter_version:
                    kalman.reset(estimate=measurement_ft, timestamp=timestamp)
                    last_altimeter_version = snapshot.altimeter_version
                altitude_ft, vertical_speed_fpm = kalman.update(measurement_ft, timestamp)
                print(
                    "[altitude-debug] "
                    f"raw={raw_altitude_ft:.1f} ft "
                    f"measurement={measurement_ft:.1f} ft "
                    f"filtered={altitude_ft:.1f} ft "
                    f"vsi={vertical_speed_fpm:.1f} fpm "
                    f"altimeter={snapshot.altimeter_setting_inhg:.2f} "
                    f"offset={snapshot.calibration_offset_ft:+.1f}",
                    flush=True,
                )
                state.update_altitude(
                    AltitudeEstimate(
                        altitude_ft=altitude_ft,
                        raw_baro_altitude_ft=raw_altitude_ft,
                        vertical_speed_fpm=vertical_speed_fpm,
                        altimeter_setting_inhg=snapshot.altimeter_setting_inhg,
                        calibration_offset_ft=snapshot.calibration_offset_ft,
                        timestamp=timestamp,
                    )
                )
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "altitude", exc, last_fault_at)

        _wait(stop_event, ALTITUDE_PERIOD_SECONDS)


def diagnostics_worker(
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    last_fault_at = 0.0
    last_uat_counters: dict[str, int] = {}
    last_tower_at = 0.0
    weather_thread = threading.Thread(
        target=lambda: weather_diagnostics_worker(events, stop_event),
        name="spaceavoider-weather-diagnostics",
        daemon=True,
    )
    weather_thread.start()

    print("[diagnostics] intensive Stratux diagnostics enabled", flush=True)
    while not stop_event.is_set():
        try:
            status = stratux_helper.get_status(timeout_seconds=2.0)
            print(_format_status_diagnostics(status, last_uat_counters), flush=True)
            last_uat_counters = _uat_counters(status)

            situation = stratux_helper.get_situation(timeout_seconds=2.0)
            print(_format_situation_diagnostics(situation), flush=True)

            now = time.monotonic()
            if now - last_tower_at >= DIAGNOSTIC_TOWER_PERIOD_SECONDS:
                last_tower_at = now
                try:
                    towers = stratux_helper.get_towers(timeout_seconds=2.0)
                    print(f"[stratux-tower-debug] {summarize_json(towers, max_chars=1800)}", flush=True)
                except Exception as exc:
                    print(f"[stratux-tower-debug] unavailable: {exc}", flush=True)
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "diagnostics", exc, last_fault_at)

        _wait(stop_event, DIAGNOSTIC_STATUS_PERIOD_SECONDS)


def weather_diagnostics_worker(
    events: queue.Queue[RuntimeEvent],
    stop_event: threading.Event,
) -> None:
    last_fault_at = 0.0
    last_timeout_log_at = 0.0
    while not stop_event.is_set():
        try:
            print(f"[weather-debug] connecting {metar_helper.STRATUX_WEATHER_WS_URL}", flush=True)
            with metar_helper._WeatherWebSocket(metar_helper.STRATUX_WEATHER_WS_URL, 3.0) as websocket:
                print("[weather-debug] connected to Stratux /weather websocket", flush=True)
                while not stop_event.is_set():
                    message_text = websocket.receive_text(timeout_seconds=1.0)
                    if message_text is None:
                        now = time.monotonic()
                        if now - last_timeout_log_at >= DIAGNOSTIC_WEATHER_TIMEOUT_LOG_SECONDS:
                            last_timeout_log_at = now
                            print("[weather-debug] no /weather messages in recent listen window", flush=True)
                        continue

                    message = metar_helper._parse_weather_message(message_text)
                    if message is None:
                        print(f"[weather-raw] unparsable {message_text[:1000]!r}", flush=True)
                        continue

                    print(
                        "[weather-raw] "
                        f"type={message.get('Type')} location={message.get('Location')} "
                        f"time={message.get('Time')} local_received={message.get('LocaltimeReceived')} "
                        f"data={str(message.get('Data', ''))[:500]!r} "
                        f"raw={summarize_json(message, max_chars=2000)}",
                        flush=True,
                    )
        except (Exception, SystemExit) as exc:
            last_fault_at = _emit_fault(events, "weather-diagnostics", exc, last_fault_at)
            _wait(stop_event, 5.0)


def _emit_fault(
    events: queue.Queue[RuntimeEvent],
    worker: str,
    exc: BaseException,
    last_fault_at: float,
) -> float:
    now = time.time()
    if now - last_fault_at >= FAULT_THROTTLE_SECONDS:
        events.put(SensorFaultEvent(worker=worker, message=str(exc), timestamp=now))
        return now
    return last_fault_at


def _wait(stop_event: threading.Event, seconds: float) -> None:
    stop_event.wait(max(0.001, seconds))


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_gps_debug(readings: dict, fix: dict) -> str:
    satellites = fix.get("satellites")
    used_prns = []
    visible_prns = []
    if isinstance(satellites, list):
        for satellite in satellites:
            prn = satellite.get("PRN") or satellite.get("prn") or satellite.get("gnssid")
            if prn is not None:
                visible_prns.append(str(prn))
                if satellite.get("used"):
                    used_prns.append(str(prn))

    return (
        "[gps-debug] "
        f"source={readings.get('source')} "
        f"mode={fix.get('mode')} mode_name={fix.get('mode_name')} "
        f"fix2d={fix.get('has_2d_fix')} fix3d={fix.get('has_3d_fix')} "
        f"lat={fix.get('latitude_deg')} lon={fix.get('longitude_deg')} "
        f"alt_m={fix.get('altitude_m')} alt_msl_m={fix.get('altitude_msl_m')} alt_hae_m={fix.get('altitude_hae_m')} "
        f"speed_mps={fix.get('ground_speed_mps')} speed_stratux={fix.get('ground_speed_stratux')} "
        f"track={fix.get('track_deg')} climb={fix.get('climb_mps') or fix.get('climb_stratux')} "
        f"hdop={fix.get('horizontal_dop')} vdop={fix.get('vertical_dop')} pdop={fix.get('position_dop')} "
        f"sat_used={fix.get('satellites_used')} sat_visible={fix.get('satellites_visible')} "
        f"sat_tracked={fix.get('satellites_tracked')} used_prns={','.join(used_prns)} visible_prns={','.join(visible_prns)} "
        f"device={fix.get('device_path')} driver={fix.get('device_driver')} subtype={fix.get('device_subtype')} "
        f"gpsd_error={readings.get('gpsd_error')}"
    )


def _uat_counters(status: dict) -> dict[str, int]:
    counters = {}
    for key, value in status.items():
        if key.startswith("UAT_"):
            try:
                counters[key] = int(value)
            except (TypeError, ValueError):
                pass
    return counters


def _format_status_diagnostics(status: dict, previous_uat: dict[str, int]) -> str:
    counters = _uat_counters(status)
    deltas = {
        key: value - previous_uat.get(key, value)
        for key, value in counters.items()
        if key.endswith("_total") or key in {"UAT_messages_total", "UAT_messages_last_minute", "UAT_messages_max"}
    }
    return (
        "[stratux-status-debug] "
        f"time={stratux_helper.now_text()} "
        f"uat_connected={status.get('UATRadio_connected')} "
        f"uat_total={status.get('UAT_messages_total')} "
        f"uat_last_min={status.get('UAT_messages_last_minute')} "
        f"uat_max={status.get('UAT_messages_max')} "
        f"uat_metar={status.get('UAT_METAR_total')} "
        f"uat_taf={status.get('UAT_TAF_total')} "
        f"uat_nexrad={status.get('UAT_NEXRAD_total')} "
        f"uat_notam={status.get('UAT_NOTAM_total')} "
        f"uat_pirep={status.get('UAT_PIREP_total')} "
        f"uat_sigmet={status.get('UAT_SIGMET_total')} "
        f"uat_other={status.get('UAT_OTHER_total')} "
        f"uat_traffic={status.get('UAT_traffic_targets_tracking')} "
        f"deltas={deltas} "
        f"gps_connected={status.get('GPS_connected')} "
        f"gps_solution={status.get('GPS_solution')} "
        f"gps_locked={status.get('GPS_satellites_locked')} "
        f"gps_seen={status.get('GPS_satellites_seen')} "
        f"gps_tracked={status.get('GPS_satellites_tracked')} "
        f"gps_accuracy={status.get('GPS_position_accuracy')} "
        f"cpu_temp={status.get('CPUTemp')}"
    )


def _format_situation_diagnostics(situation: dict) -> str:
    return (
        "[stratux-situation-debug] "
        f"gps_fix_quality={situation.get('GPSFixQuality')} "
        f"lat={situation.get('GPSLatitude')} lon={situation.get('GPSLongitude')} "
        f"alt_msl={situation.get('GPSAltitudeMSL')} hae={situation.get('GPSHeightAboveEllipsoid')} "
        f"speed={situation.get('GPSGroundSpeed')} course={situation.get('GPSTrueCourse')} "
        f"vertical_speed={situation.get('GPSVerticalSpeed')} "
        f"sats={situation.get('GPSSatellites')} seen={situation.get('GPSSatellitesSeen')} tracked={situation.get('GPSSatellitesTracked')} "
        f"hacc={situation.get('GPSHorizontalAccuracy')} vacc={situation.get('GPSVerticalAccuracy')} "
        f"last_nmea={situation.get('GPSLastValidNMEAMessage')!r} "
        f"last_nmea_time={situation.get('GPSLastValidNMEAMessageTime')} "
        f"baro_alt={situation.get('BaroPressureAltitude')} baro_temp={situation.get('BaroTemperature')} "
        f"baro_source={situation.get('BaroSourceType')}"
    )


def summarize_json(data: object, max_chars: int = 1000) -> str:
    try:
        text = json.dumps(data, sort_keys=True, default=str)
    except TypeError:
        text = repr(data)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text
