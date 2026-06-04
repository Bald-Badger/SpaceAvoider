"""Timed cockpit reminder callouts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time


FUEL_REMINDER_INITIAL_SECONDS = 30 * 60.0
FUEL_REMINDER_PERIOD_SECONDS = 30 * 60.0
WATER_REMINDER_INITIAL_SECONDS = 20 * 60.0
WATER_REMINDER_PERIOD_SECONDS = 30 * 60.0


@dataclass
class VoiceReminder:
    name: str
    audio_file: Path
    next_due_at: float
    period_seconds: float


class VoiceReminderController:
    """Play simple periodic voice reminders without queueing audio."""

    def __init__(
        self,
        audio_player,
        fuel_audio: Path,
        water_audio: Path,
        now: float | None = None,
    ) -> None:
        started_at = time.monotonic() if now is None else now
        self.audio_player = audio_player
        self.reminders = [
            VoiceReminder(
                name="switch fuel tank",
                audio_file=fuel_audio,
                next_due_at=started_at + FUEL_REMINDER_INITIAL_SECONDS,
                period_seconds=FUEL_REMINDER_PERIOD_SECONDS,
            ),
            VoiceReminder(
                name="drink water",
                audio_file=water_audio,
                next_due_at=started_at + WATER_REMINDER_INITIAL_SECONDS,
                period_seconds=WATER_REMINDER_PERIOD_SECONDS,
            ),
        ]

    @property
    def audio_files(self) -> tuple[Path, ...]:
        return tuple(reminder.audio_file for reminder in self.reminders)

    def tick(self) -> None:
        now = time.monotonic()
        for reminder in self.reminders:
            if now < reminder.next_due_at:
                continue

            self.play(reminder)
            while reminder.next_due_at <= now:
                reminder.next_due_at += reminder.period_seconds

    def play(self, reminder: VoiceReminder) -> None:
        print(f"[reminder] {reminder.name}", flush=True)
        if self.audio_player is None:
            print(f"[reminder] audio disabled: {reminder.audio_file}", flush=True)
            return

        try:
            self.audio_player.play_preloaded_now(reminder.audio_file)
        except (Exception, SystemExit) as exc:
            print(f"[fault] audio: {exc}", flush=True)

