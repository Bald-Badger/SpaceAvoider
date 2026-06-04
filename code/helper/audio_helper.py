"""Audio playback glue for the native SpaceAvoider audio player."""

from __future__ import annotations

import argparse
import os
import select
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALLOUT = PROJECT_ROOT / "audio" / "GeoFS-alerts" / "audio" / "airbus-autopilot-off.mp3"
DEFAULT_AUDIO_DEVICE = "bcm2835 Headphones, bcm2835 Headphones"
DEFAULT_AUDIO_BINARY = PROJECT_ROOT / "build" / "audio_player"
DEFAULT_START_TIMEOUT_SECONDS = 5.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class AudioPlaybackConfig:
    audio_file: Path = DEFAULT_CALLOUT
    audio_device: str | None = DEFAULT_AUDIO_DEVICE
    volume: float = 1.0
    audio_binary: Path = DEFAULT_AUDIO_BINARY


class InterruptingAudioPlayer:
    """Non-queueing native audio player for short cockpit callouts.

    Python stays as orchestration glue. The hot audio path lives in
    ``native/audio_player.cpp`` and receives simple stdin commands.
    """

    def __init__(
        self,
        audio_device: str | None = DEFAULT_AUDIO_DEVICE,
        volume: float = 1.0,
        audio_binary: Path = DEFAULT_AUDIO_BINARY,
    ) -> None:
        self.audio_device = audio_device
        self.volume = _clamp_volume(volume)
        self.audio_binary = audio_binary
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return

            binary = _require_audio_binary(self.audio_binary)
            command = [str(binary), "--server", "--volume", str(self.volume)]
            process_env = _audio_process_env(self.audio_device)
            if self.audio_device and not _is_bluealsa_device(self.audio_device):
                command.extend(["--device", self.audio_device])

            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=_audio_process_stderr(self.audio_device),
                env=process_env,
                text=True,
                bufsize=1,
            )
            ready = self._read_response(timeout=DEFAULT_START_TIMEOUT_SECONDS)
            if ready != "READY":
                self.close()
                raise SystemExit(f"native audio player did not start cleanly: {ready}")

    def preload(self, audio_files: list[Path] | tuple[Path, ...]) -> None:
        self.start()
        for audio_file in audio_files:
            self._send("PRELOAD", self._audio_path(audio_file))

    def play_now(self, audio_file: Path) -> None:
        self.start()
        self._send("PLAY", self._audio_path(audio_file))

    def play_preloaded_now(self, audio_file: Path) -> None:
        self.start()
        self._send("PLAY_PRELOADED", self._audio_path(audio_file))

    def play_preloaded_and_wait(self, audio_file: Path) -> None:
        self.start()
        self._send("PLAY_PRELOADED_BLOCKING", self._audio_path(audio_file))

    def stop(self) -> None:
        if self._process is None:
            return

        self._send("STOP")

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None

            if process is None:
                return

            if process.poll() is None:
                try:
                    assert process.stdin is not None
                    process.stdin.write("QUIT\n")
                    process.stdin.flush()
                    self._read_response_from(process, timeout=1.0)
                    process.wait(timeout=1.0)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()

    def _send(self, command: str, argument: Path | None = None) -> str:
        with self._lock:
            self.start()
            process = self._live_process()
            assert process.stdin is not None

            line = command if argument is None else f"{command} {argument}"
            try:
                process.stdin.write(f"{line}\n")
                process.stdin.flush()
            except BrokenPipeError as exc:
                raise SystemExit("native audio player stopped accepting commands") from exc

            response = self._read_response(timeout=DEFAULT_COMMAND_TIMEOUT_SECONDS)
            if response.startswith("ERR "):
                raise SystemExit(response[4:])
            if not response.startswith("OK"):
                raise SystemExit(f"unexpected native audio response: {response}")
            return response

    def _read_response(self, timeout: float) -> str:
        process = self._live_process()
        return self._read_response_from(process, timeout)

    def _read_response_from(self, process: subprocess.Popen[str], timeout: float) -> str:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            raise SystemExit("native audio player did not respond in time")

        line = process.stdout.readline()
        if not line:
            raise SystemExit("native audio player exited without a response")

        return line.strip()

    def _live_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise SystemExit("native audio player is not running")
        if self._process.poll() is not None:
            raise SystemExit(f"native audio player exited with code {self._process.returncode}")

        return self._process

    def _audio_path(self, audio_file: Path) -> Path:
        audio_path = audio_file.expanduser().resolve()
        if not audio_path.is_file():
            raise SystemExit(f"Audio file does not exist: {audio_path}")

        return audio_path


def play_audio_clip(config: AudioPlaybackConfig | None = None) -> None:
    """Play one clip through the native C++ helper and wait for it to finish."""

    config = config or AudioPlaybackConfig()
    audio_file = config.audio_file.expanduser().resolve()
    if not audio_file.is_file():
        raise SystemExit(f"Audio file does not exist: {audio_file}")

    binary = _require_audio_binary(config.audio_binary)
    command = [str(binary), "--play", str(audio_file), "--volume", str(_clamp_volume(config.volume))]
    process_env = _audio_process_env(config.audio_device)
    if config.audio_device and not _is_bluealsa_device(config.audio_device):
        command.extend(["--device", config.audio_device])

    print(f"playing audio: {audio_file}")
    subprocess.run(command, check=True, env=process_env, stderr=_audio_process_stderr(config.audio_device))


def get_audio_devices(audio_binary: Path = DEFAULT_AUDIO_BINARY) -> list[str]:
    binary = _require_audio_binary(audio_binary)
    result = subprocess.run(
        [str(binary), "--list-devices"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_audio_devices(audio_binary: Path = DEFAULT_AUDIO_BINARY) -> None:
    for device in get_audio_devices(audio_binary):
        print(device)


def _require_audio_binary(audio_binary: Path) -> Path:
    binary = audio_binary.expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(
            "Native audio player is not built yet. Run setup or build it manually:\n"
            "  sudo bash scripts/setup_pi_overlay.sh\n"
            "  bash scripts/build_native.sh"
        )

    return binary


def _clamp_volume(volume: float) -> float:
    return max(0.0, min(1.0, volume))


def _audio_process_env(audio_device: str | None) -> dict[str, str] | None:
    if not audio_device or not _is_bluealsa_device(audio_device):
        return None

    env = os.environ.copy()
    env["SDL_AUDIODRIVER"] = "alsa"
    env["AUDIODEV"] = audio_device
    return env


def _audio_process_stderr(audio_device: str | None):
    if audio_device and _is_bluealsa_device(audio_device):
        return subprocess.DEVNULL

    return None


def _is_bluealsa_device(audio_device: str) -> bool:
    return audio_device.startswith("bluealsa:")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play a SpaceAvoider audio callout through the C++ helper.")
    parser.add_argument(
        "audio_file",
        nargs="?",
        type=Path,
        default=DEFAULT_CALLOUT,
        help="audio file to play",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_AUDIO_DEVICE,
        help="SDL audio device name; defaults to Raspberry Pi headphone jack",
    )
    parser.add_argument(
        "--system-default",
        action="store_true",
        help="use the system default audio output instead of forcing the headphone jack",
    )
    parser.add_argument("--volume", type=float, default=1.0, help="playback volume from 0.0 to 1.0")
    parser.add_argument("--binary", type=Path, default=DEFAULT_AUDIO_BINARY, help="native audio helper binary")
    parser.add_argument("--list-devices", action="store_true", help="list SDL audio playback devices")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list_devices:
        list_audio_devices(args.binary)
        return

    audio_device = None if args.system_default else args.device
    play_audio_clip(
        AudioPlaybackConfig(
            audio_file=args.audio_file,
            audio_device=audio_device,
            volume=args.volume,
            audio_binary=args.binary,
        )
    )


if __name__ == "__main__":
    main()
