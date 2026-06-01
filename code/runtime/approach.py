"""Approach mode and altitude callout logic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from code.runtime.altitude import pressure_to_altitude_ft
from code.runtime.state import RuntimeState
from code.runtime.workers import update_metar_altimeter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CALLOUT_AUDIO_DIR = PROJECT_ROOT / "audio" / "GeoFS-alerts" / "audio"
APPROACH_MODE_AUDIO = PROJECT_ROOT / "audio" / "ai_gen" / "Approach Mode.wav"
APPROACH_MODE_TERMINATE_AUDIO = PROJECT_ROOT / "audio" / "ai_gen" / "Approach Mode terminate.wav"
CALLOUT_THRESHOLDS_FT = (2500, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, 10, 5)
APPROACH_AUTO_TERMINATE_AFTER_100_SECONDS = 30.0


@dataclass(frozen=True)
class ApproachCallout:
    threshold_ft: int
    audio_file: Path
    agl_ft: float


class ApproachModeController:
    """Track descent through AGL thresholds and trigger callout audio."""

    def __init__(
        self,
        state: RuntimeState,
        audio_player,
        callout_thresholds_ft: tuple[int, ...] = CALLOUT_THRESHOLDS_FT,
        audio_dir: Path = CALLOUT_AUDIO_DIR,
        approach_mode_audio: Path = APPROACH_MODE_AUDIO,
        approach_mode_terminate_audio: Path = APPROACH_MODE_TERMINATE_AUDIO,
    ) -> None:
        self.state = state
        self.audio_player = audio_player
        self.callout_thresholds_ft = callout_thresholds_ft
        self.audio_dir = audio_dir
        self.approach_mode_audio = approach_mode_audio
        self.approach_mode_terminate_audio = approach_mode_terminate_audio
        self.active = False
        self.start_altitude_ft: float | None = None
        self.start_agl_ft: float | None = None
        self.previous_agl_ft: float | None = None
        self.has_reached_100_ft = False
        self.auto_terminate_at: float | None = None

    @property
    def audio_files(self) -> tuple[Path, ...]:
        return (
            self.approach_mode_audio,
            self.approach_mode_terminate_audio,
            *(self.audio_file_for_threshold(threshold) for threshold in self.callout_thresholds_ft),
        )

    def enter(self) -> None:
        try:
            sample = update_metar_altimeter(self.state)
            print(
                f"[approach] refreshed METAR altimeter={sample.altimeter_inhg:.2f} inHg before entry",
                flush=True,
            )
        except (Exception, SystemExit) as exc:
            print(f"[approach] METAR refresh failed before entry: {exc}", flush=True)

        snapshot = self.state.snapshot()
        current_altitude_ft = self.current_altitude_ft(snapshot)
        if current_altitude_ft is None:
            print("[approach] cannot enter approach mode: altitude estimate unavailable", flush=True)
            return

        agl_ft = current_altitude_ft - snapshot.known_altitude_ft
        self.active = True
        self.start_altitude_ft = current_altitude_ft
        self.start_agl_ft = agl_ft
        self.previous_agl_ft = agl_ft
        self.has_reached_100_ft = agl_ft <= 100.0
        self.auto_terminate_at = None
        self.state.set_approach_mode(True)

        print(
            "[approach] entered "
            f"altitude={current_altitude_ft:.1f} ft "
            f"agl={agl_ft:.1f} ft",
            flush=True,
        )
        print("[approach] BEEP_PLACEHOLDER entered approach mode", flush=True)
        self.play_preloaded(self.approach_mode_audio)

    def exit(self, manual: bool = False) -> None:
        self.active = False
        self.start_altitude_ft = None
        self.start_agl_ft = None
        self.previous_agl_ft = None
        self.has_reached_100_ft = False
        self.auto_terminate_at = None
        self.state.set_approach_mode(False)
        print("[approach] exited", flush=True)
        if manual:
            print("[approach] BEEP_PLACEHOLDER exited approach mode by manual cancel", flush=True)
            self.play_preloaded(self.approach_mode_terminate_audio)
        else:
            print("[approach] auto-terminated after 100 ft callout window", flush=True)

    def tick(self) -> None:
        if not self.active:
            return

        if self.auto_terminate_at is not None and time.monotonic() >= self.auto_terminate_at:
            self.exit(manual=False)
            return

        snapshot = self.state.snapshot()
        current_altitude_ft = self.current_altitude_ft(snapshot)
        if current_altitude_ft is None:
            return

        current_agl_ft = current_altitude_ft - snapshot.known_altitude_ft
        previous_agl_ft = self.previous_agl_ft
        self.previous_agl_ft = current_agl_ft

        if previous_agl_ft is None or current_agl_ft == previous_agl_ft:
            return

        crossed = self.crossed_thresholds(previous_agl_ft, current_agl_ft)
        for threshold in crossed:
            callout = ApproachCallout(
                threshold_ft=threshold,
                audio_file=self.audio_file_for_threshold(threshold),
                agl_ft=current_agl_ft,
            )
            self.play_callout(callout)
            if threshold == 100 and current_agl_ft <= previous_agl_ft:
                self.has_reached_100_ft = True
                self.auto_terminate_at = time.monotonic() + APPROACH_AUTO_TERMINATE_AFTER_100_SECONDS
                print(
                    "[approach] 100 ft reached; auto terminate armed in "
                    f"{APPROACH_AUTO_TERMINATE_AFTER_100_SECONDS:.0f}s",
                    flush=True,
                )

    def crossed_thresholds(self, previous_agl_ft: float, current_agl_ft: float) -> tuple[int, ...]:
        """Return thresholds crossed between samples.

        Descent emits every crossed callout from high to low. Climb emits only
        the highest crossed callout, which keeps the useful "50 again" behavior
        without playing every lower threshold during a go-around/climb.
        """

        if current_agl_ft < previous_agl_ft:
            return tuple(
                threshold
                for threshold in self.callout_thresholds_ft
                if previous_agl_ft > threshold >= current_agl_ft
            )

        if self.has_reached_100_ft:
            return ()

        crossed = tuple(
            threshold
            for threshold in self.callout_thresholds_ft
            if previous_agl_ft < threshold <= current_agl_ft
        )
        return crossed[:1]

    def play_callout(self, callout: ApproachCallout) -> None:
        print(f"[approach] callout {callout.threshold_ft} ft AGL", flush=True)
        if self.audio_player is None:
            print(f"[approach] audio disabled: {callout.audio_file}", flush=True)
            return

        self.play_preloaded(callout.audio_file)

    def audio_file_for_threshold(self, threshold_ft: int) -> Path:
        return self.audio_dir / f"{threshold_ft}.mp3"

    def play_preloaded(self, audio_file: Path) -> None:
        if self.audio_player is None:
            return

        try:
            self.audio_player.play_preloaded_now(audio_file)
        except (Exception, SystemExit) as exc:
            print(f"[fault] audio: {exc}", flush=True)

    def current_altitude_ft(self, snapshot) -> float | None:
        if snapshot.pressure_average is not None:
            raw_altitude_ft = pressure_to_altitude_ft(
                snapshot.pressure_average.pressure_pa,
                snapshot.altimeter_setting_inhg,
            )
            return raw_altitude_ft + snapshot.calibration_offset_ft

        if snapshot.altitude is not None:
            return snapshot.altitude.altitude_ft

        return None
