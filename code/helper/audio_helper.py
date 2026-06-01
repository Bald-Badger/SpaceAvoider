"""Audio playback helpers for SpaceAvoider callouts."""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALLOUT = PROJECT_ROOT / "audio" / "airbus_retard_retard.wav"
DEFAULT_AUDIO_DEVICE = "bcm2835 Headphones, bcm2835 Headphones"


@dataclass(frozen=True)
class AudioPlaybackConfig:
    audio_file: Path = DEFAULT_CALLOUT
    audio_device: str | None = DEFAULT_AUDIO_DEVICE
    volume: float = 1.0


class InterruptingAudioPlayer:
    """Non-queueing pygame mixer player for short cockpit callouts."""

    def __init__(self, audio_device: str | None = DEFAULT_AUDIO_DEVICE, volume: float = 1.0) -> None:
        self.audio_device = audio_device
        self.volume = _clamp_volume(volume)
        self._pygame = _import_pygame()
        self._initialized = False
        self._sounds: dict[Path, object] = {}

    def start(self) -> None:
        if self._initialized:
            return

        _init_mixer(self._pygame, self.audio_device)
        self._initialized = True

    def preload(self, audio_files: list[Path] | tuple[Path, ...]) -> None:
        """Decode and cache clips in memory before time-critical playback."""

        self.start()
        for audio_file in audio_files:
            self._load_sound(audio_file)

    def play_now(self, audio_file: Path) -> None:
        """Stop any current callout and immediately play ``audio_file``.

        This falls back to decoding/loading the clip if it was not preloaded.
        Time-critical paths should call ``play_preloaded_now`` instead.
        """

        self.start()
        sound = self._load_sound(audio_file)
        self._play_sound(sound)

    def play_preloaded_now(self, audio_file: Path) -> None:
        """Stop any current callout and play an already decoded cached clip."""

        self.start()
        audio_file = audio_file.expanduser().resolve()
        sound = self._sounds.get(audio_file)
        if sound is None:
            raise SystemExit(f"Audio file was not preloaded: {audio_file}")

        self._play_sound(sound)

    def _play_sound(self, sound) -> None:
        sound.set_volume(self.volume)
        self._pygame.mixer.stop()
        channel = sound.play()
        if channel is None:
            raise SystemExit("pygame.mixer could not start audio playback")

    def stop(self) -> None:
        if self._initialized:
            self._pygame.mixer.stop()

    def close(self) -> None:
        if self._initialized:
            self._pygame.mixer.quit()
            self._initialized = False
        self._sounds.clear()

    def _load_sound(self, audio_file: Path):
        audio_file = audio_file.expanduser().resolve()
        if not audio_file.is_file():
            raise SystemExit(f"Audio file does not exist: {audio_file}")

        sound = self._sounds.get(audio_file)
        if sound is None:
            sound = self._pygame.mixer.Sound(str(audio_file))
            self._sounds[audio_file] = sound
        return sound


def play_audio_clip(config: AudioPlaybackConfig | None = None) -> None:
    """Play one WAV/OGG/MP3 clip through pygame.mixer and wait for it to finish."""

    config = config or AudioPlaybackConfig()
    audio_file = config.audio_file.expanduser().resolve()

    if not audio_file.is_file():
        raise SystemExit(f"Audio file does not exist: {audio_file}")

    pygame = _import_pygame()
    _init_mixer(pygame, config.audio_device)

    try:
        sound = pygame.mixer.Sound(str(audio_file))
        sound.set_volume(_clamp_volume(config.volume))
        channel = sound.play()
        if channel is None:
            raise SystemExit("pygame.mixer could not start audio playback")

        print(f"playing audio: {audio_file}")
        while channel.get_busy():
            time.sleep(0.05)
    finally:
        pygame.mixer.quit()


def _init_mixer(pygame, audio_device: str | None) -> None:
    try:
        pygame.mixer.init(devicename=audio_device)
    except pygame.error as exc:
        device_text = audio_device or "system default"
        raise SystemExit(
            f"pygame.mixer could not open audio device {device_text!r}. Check the Pi audio "
            "output configuration, speaker connection, and ALSA/PulseAudio availability."
        ) from exc


def _clamp_volume(volume: float) -> float:
    return max(0.0, min(1.0, volume))


def _import_pygame():
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    try:
        import pygame
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "pygame is not installed. Run setup, activate the venv, then try again:\n"
            "  sudo bash scripts/setup_pi_overlay.sh\n"
            "  source .venv/bin/activate"
        ) from exc

    return pygame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play a SpaceAvoider audio callout.")
    parser.add_argument(
        "audio_file",
        nargs="?",
        type=Path,
        default=DEFAULT_CALLOUT,
        help="audio file to play; defaults to audio/airbus_retard_retard.wav",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_AUDIO_DEVICE,
        help="pygame/SDL audio device name; defaults to Raspberry Pi headphone jack",
    )
    parser.add_argument(
        "--system-default",
        action="store_true",
        help="use the system default audio output instead of forcing the headphone jack",
    )
    parser.add_argument("--volume", type=float, default=1.0, help="playback volume from 0.0 to 1.0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audio_device = None if args.system_default else args.device
    play_audio_clip(
        AudioPlaybackConfig(audio_file=args.audio_file, audio_device=audio_device, volume=args.volume)
    )


if __name__ == "__main__":
    main()
