# SpaceAvoider

Personal Raspberry Pi / Stratux toy traffic-awareness and avionics-display project.

## Persistent Pi Setup

Persistent Stratux/Raspberry Pi setup starts by manually disabling overlay
protection and rebooting.

Disable overlay protection, then reboot:

```bash
sudo overlayctl disable
sudo reboot
```

Run the setup script:

```bash
cd /rwbase/playground/SpaceAvoider
sudo bash scripts/setup_pi_overlay.sh
```

The setup script currently:

1. Checks/corrects the Pi system clock using the HTTP `Date` header from `http://deb.debian.org/debian/`.
2. Runs `apt-get update`, `apt-get upgrade -y`, `apt-get autoremove -y`, and `apt-get clean`.
3. Skips the Argon ONE driver install for now; the installer call is left commented in the setup script.
4. Installs `build-essential`, `python3-dev`, `python3-full`, Raspberry Pi/Debian `python3-pygame` for audio playback, GPIO support libraries, and BlueZ Bluetooth tools.
5. Creates/updates the project Python virtual environment at `.venv` with system site packages enabled.
6. Installs project PyPI packages into `.venv`, including the BMP581, DHT11, and matrix keypad helpers.
7. Installs and enables the `spaceavoider.service` systemd service so the runtime starts on boot.

All project Python should run from `.venv`, not directly from system Python:

```bash
source /rwbase/playground/SpaceAvoider/.venv/bin/activate
python -c "import sys, pygame; print(sys.executable); print(pygame.version.ver)"
```

Display rendering is intentionally not implemented in Python now. The old
pygame/framebuffer experiment was removed; future Python display code should be
glue logic only, with performance-sensitive rendering implemented in C++.
`python3-pygame` remains installed for `audio_helper.py` only.

Print the placeholder display command:

```bash
cd /rwbase/playground/SpaceAvoider
source .venv/bin/activate
python -m code.helper.display_helper --print-command
```

Play the first audio callout helper:

```bash
python -m code.helper.audio_helper --volume 0.8
```

The audio helper defaults to the Raspberry Pi headphone jack:
`bcm2835 Headphones, bcm2835 Headphones`.

Read the BMP581 pressure sensor:

```bash
python -m code.helper.pressure_helper --samples 3 --interval 1
```

Read the DHT11 temperature/humidity module on GPIO17:

```bash
python -m code.helper.humidity_helper
```

For a more patient DHT11 read, add retries:

```bash
python -m code.helper.humidity_helper --retries 3 --retry-delay 2
```

Print debounced 4x4 matrix keypad presses:

```bash
python -m code.helper.keypad_helper
```

List nearby Bluetooth devices:

```bash
python -m code.helper.bluetooth_helper --seconds 10
```

Filter for a known device name, such as a SoundCore speaker:

```bash
python -m code.helper.bluetooth_helper --seconds 20 --transport bredr --name SoundCore
```

Run the first full runtime framework:

```bash
python -m code.runtime.main
```

The setup script also enables the runtime as a boot service. Useful service
commands:

```bash
sudo systemctl start spaceavoider.service
sudo systemctl stop spaceavoider.service
sudo systemctl restart spaceavoider.service
sudo systemctl status spaceavoider.service
journalctl -u spaceavoider.service -f
```

Runtime output is also mirrored into a timestamped repo log on every program
start, such as `log/SpaceAvoider_log_05_31_2026-02_41.log`. If the program
starts again during the same minute, that minute's log is overwritten.

```bash
ls -lt log/
tail -f log/SpaceAvoider_log_*.log
```

Runtime worker schedule:

- Humidity: once every 10 seconds, stores humidity only.
- Pressure: twice per second normally, then 10 times per second in approach mode; stores pressure and temperature in an 8-sample ring buffer.
- GPS: once per second when available, stores position and GPS altitude. GPS is optional and may be missing, stale, or invalid.
- METAR: every 5 minutes, instantly overwrites the current altimeter setting from the latest valid METAR. If GPS is unavailable, it uses KCHD; if GPS is available, it tries the nearest of KCHD/A39 first and falls back to the other if needed.
- Altitude tracker: twice per second, uses pressure-derived altitude through a placeholder Kalman smoother.
- Keypad: low-duty debounced scan loop, emits key press events.

Runtime behavior must not depend on GPS being available. GPS is only advisory
for sanity checks until proven reliable in the current environment.

Press `C` on the keypad to start altimeter calibration, then enter four digits
like `2992`. During calibration, `D` cancels and `*` backspaces. Entering
calibration plays `audio/ai_gen/Calibrate mode.wav` when audio is enabled. A
successful calibration plays `audio/ai_gen/calibration success.mp3`.

Press `A` to enter approach mode; press `A` again to leave approach mode. In
approach mode, AGL callouts use files from `audio/GeoFS-alerts/audio` for:

Entering approach mode immediately refreshes METAR, overwrites the current
altimeter setting when a valid METAR is available, and computes entry AGL from
the current pressure buffer with that setting. Entering approach plays
`audio/ai_gen/Approach Mode.wav`. Pressing `A` to cancel approach mode plays
`audio/ai_gen/Approach Mode terminate.wav`; automatic approach termination does
not play that terminate sound.

After the `100` ft callout is reported, approach mode auto-terminates 30 seconds
later. After reaching the `100` ft point, climb-back callouts are suppressed, so
a sequence like `50 -> 30 -> 50` does not call out the second `50`.

```text
2500, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, 10, 5
```

If a new callout happens while another one is playing, the new callout replaces
the old one instead of being queued. Approach callout files are decoded and
cached in memory during runtime startup, so threshold crossings do not decode
MP3 files on the hot path.

On program startup, audio-enabled runtime plays
`audio/GeoFS-alerts/audio/airbus-autopilot-off.mp3` as a placeholder alive
sound.

Scan which Raspberry Pi header GPIO pins currently look free:

```bash
python3 scripts/gpio_free_pins.py --free-only
```

Re-enable overlay protection, then reboot:

```bash
sudo overlayctl enable
sudo reboot
```

Check protected overlay behavior:

```bash
df -h /overlay/rwdata
```
