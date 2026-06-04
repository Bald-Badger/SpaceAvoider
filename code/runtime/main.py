"""Main runtime entry point for SpaceAvoider."""

from __future__ import annotations

import argparse
import queue
import signal
import threading
import time
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from code.helper.audio_helper import DEFAULT_AUDIO_DEVICE, InterruptingAudioPlayer
from code.helper.bluetooth_helper import ensure_device_connected
from code.runtime.audio_assets import AI_GEN_AUDIO_DIR, GEOFS_AUDIO_DIR, existing_audio_files, resolve_audio_file
from code.runtime.altitude import CalibrationInput, calibrate_altitude
from code.runtime.approach import ApproachModeController
from code.runtime.events import KeyPressedEvent, RuntimeEvent, SensorFaultEvent
from code.runtime.logging_utils import setup_runtime_log
from code.runtime.reminders import VoiceReminderController
from code.runtime.state import (
    DEFAULT_ALTIMETER_SETTING_INHG,
    DEFAULT_KNOWN_ALTITUDE_FT,
    RuntimeState,
)
from code.runtime.workers import start_runtime_workers


DEFAULT_GPS_SANITY_THRESHOLD_FT = 500.0
DEFAULT_STATUS_INTERVAL_SECONDS = 5.0
DEFAULT_BLUETOOTH_AUDIO_NAME = "SoundCore 2"
DEFAULT_BLUETOOTH_AUDIO_SCAN_SECONDS = 10.0
STARTUP_AUDIO = GEOFS_AUDIO_DIR / "airbus-autopilot-off.mp3"
CALIBRATE_MODE_AUDIO = resolve_audio_file(AI_GEN_AUDIO_DIR, "Calibrate Mode.wav", "Calibrate mode.wav")
CALIBRATE_SUCCESS_AUDIO = resolve_audio_file(
    AI_GEN_AUDIO_DIR,
    "Calibration Success.wav",
    "calibration success.mp3",
)
SWITCH_FUEL_TANK_AUDIO = resolve_audio_file(AI_GEN_AUDIO_DIR, "Switch Fuel Tank.wav")
DRINK_WATER_AUDIO = resolve_audio_file(AI_GEN_AUDIO_DIR, "Drink Water.wav")


class CalibrationController:
    def __init__(
        self,
        state: RuntimeState,
        gps_sanity_threshold_ft: float,
        audio_player=None,
        calibrate_mode_audio: Path = CALIBRATE_MODE_AUDIO,
        calibrate_success_audio: Path = CALIBRATE_SUCCESS_AUDIO,
    ) -> None:
        self.state = state
        self.gps_sanity_threshold_ft = gps_sanity_threshold_ft
        self.audio_player = audio_player
        self.calibrate_mode_audio = calibrate_mode_audio
        self.calibrate_success_audio = calibrate_success_audio
        self._collecting = False
        self._digits: list[str] = []

    @property
    def active(self) -> bool:
        return self._collecting

    def handle_key(self, key: str) -> None:
        if key == "C" and not self._collecting:
            self._collecting = True
            self._digits = []
            print("[calibration] enter 4-digit altimeter setting, like 2992", flush=True)
            self.play_calibrate_mode_audio()
            return

        if not self._collecting:
            return

        if key == "D":
            self._collecting = False
            self._digits = []
            print("[calibration] canceled", flush=True)
            return

        if key == "*":
            if self._digits:
                self._digits.pop()
            print(f"[calibration] digits={''.join(self._digits) or '-'}", flush=True)
            return

        if not key.isdigit():
            return

        self._digits.append(key)
        print(f"[calibration] digits={''.join(self._digits)}", flush=True)
        if len(self._digits) >= 4:
            self._finish("".join(self._digits[:4]))
            self._collecting = False
            self._digits = []

    def _finish(self, digits: str) -> None:
        altimeter_setting_inhg = int(digits) / 100.0
        if not 28.0 <= altimeter_setting_inhg <= 31.0:
            print(
                f"[calibration] BEEP_PLACEHOLDER invalid altimeter setting {altimeter_setting_inhg:.2f} inHg",
                flush=True,
            )
            return

        snapshot = self.state.snapshot()
        if snapshot.pressure_average is None:
            print("[calibration] BEEP_PLACEHOLDER no pressure samples available", flush=True)
            return

        humidity_percent = None if snapshot.humidity is None else snapshot.humidity.humidity_percent
        result = calibrate_altitude(
            CalibrationInput(
                known_altitude_ft=snapshot.known_altitude_ft,
                altimeter_setting_inhg=altimeter_setting_inhg,
                pressure=snapshot.pressure_average,
                humidity_percent=humidity_percent,
                gps=snapshot.gps,
                gps_sanity_threshold_ft=self.gps_sanity_threshold_ft,
            )
        )
        self.state.update_calibration(
            altimeter_setting_inhg=result.altimeter_setting_inhg,
            calibration_offset_ft=result.calibration_offset_ft,
        )

        print(
            "[calibration] "
            f"altimeter={result.altimeter_setting_inhg:.2f} inHg "
            f"raw={result.raw_altitude_ft:.1f} ft "
            f"offset={result.calibration_offset_ft:+.1f} ft "
            f"calibrated={result.calibrated_altitude_ft:.1f} ft",
            flush=True,
        )
        if result.gps_delta_ft is not None:
            print(f"[calibration] GPS delta={result.gps_delta_ft:+.1f} ft", flush=True)
        if result.gps_sanity_failed:
            print("[calibration] BEEP_PLACEHOLDER calibration result is far from GPS altitude", flush=True)
        for note in result.notes:
            print(f"[calibration] note: {note}", flush=True)
        self.play_calibrate_success_audio()

    def play_calibrate_mode_audio(self) -> None:
        self.play_preloaded_audio(self.calibrate_mode_audio)

    def play_calibrate_success_audio(self) -> None:
        self.play_preloaded_audio(self.calibrate_success_audio)

    def play_preloaded_audio(self, audio_file: Path) -> None:
        if self.audio_player is None:
            return

        try:
            self.audio_player.play_preloaded_now(audio_file)
        except (Exception, SystemExit) as exc:
            print(f"[fault] audio: {exc}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SpaceAvoider sensor/runtime framework.")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this many seconds; 0 means run forever")
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL_SECONDS,
        help="seconds between console status lines; 0 disables status output",
    )
    parser.add_argument(
        "--known-altitude-ft",
        type=float,
        default=DEFAULT_KNOWN_ALTITUDE_FT,
        help="known local altitude used for calibration",
    )
    parser.add_argument(
        "--altimeter-setting",
        type=float,
        default=DEFAULT_ALTIMETER_SETTING_INHG,
        help="initial altimeter setting in inHg",
    )
    parser.add_argument(
        "--gps-sanity-threshold-ft",
        type=float,
        default=DEFAULT_GPS_SANITY_THRESHOLD_FT,
        help="calibration GPS sanity-check threshold",
    )
    parser.add_argument("--no-humidity", action="store_true", help="disable the humidity worker")
    parser.add_argument("--no-pressure", action="store_true", help="disable the pressure worker")
    parser.add_argument("--no-gps", action="store_true", help="disable the GPS worker")
    parser.add_argument("--no-metar", action="store_true", help="disable METAR altimeter setting updates")
    parser.add_argument("--no-keypad", action="store_true", help="disable the keypad worker")
    parser.add_argument("--no-audio", action="store_true", help="disable approach callout audio playback")
    parser.add_argument(
        "--no-bluetooth-audio",
        action="store_true",
        help="skip startup Bluetooth speaker discovery and use the configured audio device",
    )
    parser.add_argument(
        "--bluetooth-audio-name",
        default=DEFAULT_BLUETOOTH_AUDIO_NAME,
        help="Bluetooth speaker name to prefer at startup",
    )
    parser.add_argument(
        "--bluetooth-scan-seconds",
        type=float,
        default=DEFAULT_BLUETOOTH_AUDIO_SCAN_SECONDS,
        help="seconds to scan for the preferred Bluetooth speaker at startup",
    )
    parser.add_argument(
        "--audio-device",
        default=DEFAULT_AUDIO_DEVICE,
        help="SDL audio device name for callouts",
    )
    parser.add_argument(
        "--system-audio-default",
        action="store_true",
        help="use the system default audio output instead of forcing the headphone jack",
    )
    parser.add_argument("--volume", type=float, default=1.0, help="callout playback volume from 0.0 to 1.0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_runtime_log()
    state = RuntimeState(
        known_altitude_ft=args.known_altitude_ft,
        altimeter_setting_inhg=args.altimeter_setting,
    )
    events: queue.Queue[RuntimeEvent] = queue.Queue()
    stop_event = threading.Event()
    audio_player = None
    if not args.no_audio:
        audio_device = select_startup_audio_device(args)
        audio_player = InterruptingAudioPlayer(audio_device=audio_device, volume=args.volume)
    calibration = CalibrationController(
        state,
        gps_sanity_threshold_ft=args.gps_sanity_threshold_ft,
        audio_player=audio_player,
    )
    approach = ApproachModeController(state, audio_player=audio_player)
    reminders = VoiceReminderController(
        audio_player=audio_player,
        fuel_audio=SWITCH_FUEL_TANK_AUDIO,
        water_audio=DRINK_WATER_AUDIO,
    )

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if audio_player is not None:
        try:
            audio_files = existing_audio_files(
                (
                    STARTUP_AUDIO,
                    CALIBRATE_MODE_AUDIO,
                    CALIBRATE_SUCCESS_AUDIO,
                    *approach.audio_files,
                    *reminders.audio_files,
                )
            )
            audio_player.preload(audio_files)
            print(f"[audio] preloaded {len(audio_files)} runtime clips", flush=True)
            audio_player.play_preloaded_now(STARTUP_AUDIO)
        except (Exception, SystemExit) as exc:
            audio_player.close()
            audio_player = None
            calibration.audio_player = None
            approach.audio_player = None
            reminders.audio_player = None
            print(f"[fault] audio: {exc}", flush=True)

    threads = start_runtime_workers(
        state,
        events,
        stop_event,
        enable_humidity=not args.no_humidity,
        enable_pressure=not args.no_pressure,
        enable_gps=not args.no_gps,
        enable_metar=not args.no_metar,
        enable_keypad=not args.no_keypad,
    )

    print("[runtime] started; press C to calibrate, A for approach mode", flush=True)
    started_at = time.monotonic()
    next_status_at = started_at + max(0.0, args.status_interval)

    try:
        while not stop_event.is_set():
            if args.seconds > 0.0 and time.monotonic() - started_at >= args.seconds:
                stop_event.set()
                break

            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                event = None

            if isinstance(event, KeyPressedEvent):
                print(f"[keypad] {event.key}", flush=True)
                if event.key == "A" and not calibration.active:
                    if approach.active:
                        approach.exit(manual=True)
                    else:
                        approach.enter()
                else:
                    calibration.handle_key(event.key)
            elif isinstance(event, SensorFaultEvent):
                print(f"[fault] {event.worker}: {event.message}", flush=True)

            approach.tick()
            reminders.tick()

            if args.status_interval > 0.0 and time.monotonic() >= next_status_at:
                print(_format_status(state), flush=True)
                next_status_at = time.monotonic() + args.status_interval
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2.0)
        if audio_player is not None:
            audio_player.close()
        print("[runtime] stopped", flush=True)


def _format_status(state: RuntimeState) -> str:
    snapshot = state.snapshot()
    pressure = snapshot.pressure_average
    humidity = snapshot.humidity
    gps = snapshot.gps
    metar = snapshot.metar
    altitude = snapshot.altitude
    approach_text = "approach=on" if snapshot.approach_mode else "approach=off"

    pressure_text = "pressure=-"
    if pressure is not None:
        pressure_text = (
            f"pressure={pressure.pressure_hpa:.2f} hPa "
            f"temp={pressure.temperature_c:.1f} C samples={pressure.sample_count}"
        )

    humidity_text = "humidity=-" if humidity is None else f"humidity={humidity.humidity_percent:.1f}%"
    altitude_text = "altitude=-"
    if altitude is not None:
        altitude_text = f"altitude={altitude.altitude_ft:.1f} ft vsi={altitude.vertical_speed_fpm:.0f} fpm"

    gps_text = "gps=-"
    if gps is not None:
        gps_altitude = "-" if gps.altitude_ft is None else f"{gps.altitude_ft:.1f} ft"
        gps_text = f"gps_fix={gps.has_fix} gps_alt={gps_altitude}"

    metar_text = "metar=-"
    if metar is not None:
        metar_text = f"metar={metar.station} {metar.altimeter_inhg:.2f} inHg"

    return f"[status] {pressure_text} {humidity_text} {altitude_text} {gps_text} {metar_text} {approach_text}"


def select_startup_audio_device(args: argparse.Namespace) -> str | None:
    fallback_device = None if args.system_audio_default else args.audio_device

    if args.system_audio_default:
        print("[audio] using system default audio output; Bluetooth speaker auto-select skipped", flush=True)
        return fallback_device

    if args.no_bluetooth_audio:
        print(f"[audio] Bluetooth speaker auto-select disabled; using {fallback_device!r}", flush=True)
        return fallback_device

    speaker_name = args.bluetooth_audio_name
    try:
        result = ensure_device_connected(
            name=speaker_name,
            scan_seconds=args.bluetooth_scan_seconds,
            transport="bredr",
        )
    except (Exception, SystemExit) as exc:
        print(f"[audio] Bluetooth speaker setup failed: {exc}; using {fallback_device!r}", flush=True)
        return fallback_device

    print(f"[audio] {result.message}", flush=True)
    if not result.connected:
        print(f"[audio] using fallback audio device {fallback_device!r}", flush=True)
        return fallback_device

    if result.device is None:
        print(f"[audio] Bluetooth speaker connected without device metadata; using {fallback_device!r}", flush=True)
        return fallback_device

    bluealsa_device = bluealsa_pcm_for_device(result.device.address)
    print(f"[audio] using Bluetooth BlueALSA output {bluealsa_device!r}", flush=True)
    return bluealsa_device


def bluealsa_pcm_for_device(address: str) -> str:
    return f"bluealsa:SRV=org.bluealsa,DEV={address.upper()},PROFILE=a2dp"


if __name__ == "__main__":
    main()
