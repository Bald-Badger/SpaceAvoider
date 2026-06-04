"""Runtime audio asset lookup."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_GEN_AUDIO_DIR = PROJECT_ROOT / "audio" / "ai_gen"
GEOFS_AUDIO_DIR = PROJECT_ROOT / "audio" / "GeoFS-alerts" / "audio"


def resolve_audio_file(
    directory: Path,
    preferred_name: str,
    *fallback_names: str,
    extensions: tuple[str, ...] = (".wav", ".mp3"),
) -> Path:
    """Find an audio file by relaxed stem matching.

    The ai-generated clips have been renamed a few times. Matching by a
    normalized stem keeps the runtime linked to the right cue while still
    returning a predictable path if the file is missing.
    """

    names = (preferred_name, *fallback_names)
    files = tuple(path for path in directory.iterdir() if path.is_file()) if directory.is_dir() else ()

    for name in names:
        requested = directory / name
        if requested.is_file():
            return requested

    for name in names:
        target_stem = _normalized_stem(Path(name).stem)
        for audio_file in files:
            if audio_file.suffix.lower() in extensions and _normalized_stem(audio_file.stem) == target_stem:
                return audio_file

    return directory / preferred_name


def existing_audio_files(audio_files: tuple[Path, ...]) -> tuple[Path, ...]:
    """Return only audio files that currently exist."""

    return tuple(audio_file for audio_file in audio_files if audio_file.is_file())


def _normalized_stem(stem: str) -> str:
    return "".join(character.lower() for character in stem if character.isalnum())

