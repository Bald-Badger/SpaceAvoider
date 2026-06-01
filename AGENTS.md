Project handoff: Raspberry Pi Stratux-based toy traffic-awareness and avionics display

Context:
This is a Raspberry Pi / Stratux hobby project for a toy situational-awareness
display and cockpit-style audio callout system. It is not certified avionics
and must not be treated as real TCAS, GPWS, radar altitude, or primary flight
instrumentation.

The agent may update this file when the codebase or hardware state changes.
Keep it current and remove stale history when it stops helping.

Current hardware/setup:
- Raspberry Pi 4B running a Stratux SD card image.
- SSH workflow has used `pi@192.168.50.42` on the development network and `pi@192.168.10.1` on the Stratux AP network.
- if the ip is not reachable, try pi@192.168.10.1
- Remote writable project path: `/rwbase/playground/SpaceAvoider`.
- Local mounted workspace: `/home/shuai/stratux_pi/SpaceAvoider`.
- Stratux is mostly headless, with HDMI display output available.
- Project Python should run from `.venv`.
- Raspberry Pi 3.5 mm headphone jack is the intended audio output.

Current repository state:
- `README.md`: setup and helper run commands.
- `PINMAP.md`: current Raspberry Pi 40-pin header wiring map.
- `scripts/setup_pi_overlay.sh`: one-time setup script for persistent installs after overlay is manually disabled.
- `scripts/spaceavoider.service.in`: systemd service template installed by setup so the runtime starts on boot.
- `scripts/gpio_free_pins.py`: scans the 40-pin header and reports GPIO pins that appear free.
- `code/helper/traffic_helper.py`: standard-library Stratux `/traffic` WebSocket client.
- `code/helper/gps_helper.py`: gpsd first, then Stratux `/getSituation` fallback.
- `code/helper/metar_helper.py`: Stratux `/weather` WebSocket client and METAR/SPECI parser.
- `code/helper/display_helper.py`: pygame drawing helper. If SDL cannot open a visible display, it renders with pygame to a surface and writes small changed patches directly to `/dev/fb0`.
- `code/helper/audio_helper.py`: pygame mixer helper that plays the default audio callout through the Raspberry Pi headphone output.
- `code/helper/pressure_helper.py`: SparkFun Qwiic BMP581 helper that reports pressure in Pa/hPa/inHg and temperature in C.
- `code/helper/humidity_helper.py`: Adafruit CircuitPython DHT helper for DHT11 temperature/humidity on GPIO17.
- `code/helper/keypad_helper.py`: Adafruit CircuitPython MatrixKeypad helper for the 4x4 keypad on GPIO27/GPIO22/GPIO23/GPIO24 rows and GPIO12/GPIO26/GPIO19/GPIO16 columns.
- `code/runtime/`: first app runtime framework with thread-safe shared state, worker threads, event queue, keypad calibration flow, and placeholder pressure-only Kalman altitude tracking.
- `log/`: runtime starts mirror stdout/stderr to `log/SpaceAvoider_log_MM_DD_YYYY-HH_MM.log`; same-minute restarts overwrite the previous log for that minute, and log files are ignored by git.
- `audio/airbus_retard_retard.wav`: first cockpit meme/test callout audio clip.
- `audio/SOURCES.md`: source and licensing notes for audio assets.

Persistent Pi setup:
- User manually disables overlay and reboots before running setup:
  - `sudo overlayctl disable`
  - `sudo reboot`
- Then run:
  - `cd /rwbase/playground/SpaceAvoider`
  - `sudo bash scripts/setup_pi_overlay.sh`
- Setup script currently:
  - refuses to run if `/` is still mounted as filesystem type `overlay`
  - checks/corrects the Pi clock using the HTTP `Date` header from `http://deb.debian.org/debian/`
  - runs `apt-get update`, `apt-get upgrade -y`, `apt-get autoremove -y`, and `apt-get clean`
  - keeps the Argon ONE installer function in the file, but the call is commented out
  - installs `build-essential`, `python3-dev`, `python3-full`, `python3-pygame`, and GPIO support libraries
  - creates/updates `.venv` with `--system-site-packages`
  - uninstalls venv-local `pygame-ce`/`pygame` so apt `python3-pygame` remains visible
  - installs project PyPI packages into `.venv`, currently `adafruit-circuitpython-dht`, `adafruit-circuitpython-matrixkeypad`, and `sparkfun-qwiic-bmp581`
  - renders and installs `/etc/systemd/system/spaceavoider.service`
  - enables `spaceavoider.service` for boot startup
- After setup, user manually re-enables overlay and reboots:
  - `sudo overlayctl enable`
  - `sudo reboot`

Display notes:
- Normal pygame display through SDL does not work reliably on this Stratux image.
- `pygame-ce` from pip only exposed `offscreen`/`dummy` during testing.
- apt `python3-pygame` still reported `kmsdrm not available` and no visible `linuxfb`.
- Working path:
  - pygame draws into a `Surface`
  - helper converts only changed regions
  - helper writes those patches to `/dev/fb0`
- Pi framebuffer observed during tests:
  - `/dev/fb0`
  - size `1824x984`
  - 32 bpp
  - stride `7296`
- Display smoke test:
  - `python -m code.helper.display_helper --backend fb0`
  - default `python -m code.helper.display_helper` also falls back to `/dev/fb0`

Audio notes:
- `audio_helper.py` uses `pygame.mixer`.
- `aplay -l` showed HDMI as card 0 and 3.5 mm jack as card 1 `Headphones`.
- SDL/pygame names the headphone output:
  - `bcm2835 Headphones, bcm2835 Headphones`
- `audio_helper.py` defaults to that device.
- Use `--system-default` only when intentionally testing the Pi default output.
- Verified command:
  - `python -m code.helper.audio_helper --volume 0.8`

BMP581 pressure sensor:
- Sensor: SparkFun Qwiic BMP581 pressure sensor.
- Wiring:
  - GND -> physical pin 6
  - 3V3 -> physical pin 1
  - SDA -> GPIO2 / SDA1 / physical pin 3
  - SCL -> GPIO3 / SCL1 / physical pin 5
- Uses PyPI package `sparkfun-qwiic-bmp581`, imported as `qwiic_bmp581`.
- Driver detected the sensor at default address `0x47`.
- `i2cdetect` was not installed on the Pi during the pressure-helper test.
- The immediate first register read after `begin()` can return placeholder startup data around `130558 Pa` and `127.5 C`.
- `pressure_helper.py` initializes once, waits briefly, then retries invalid startup data.
- Verified command:
  - `python -m code.helper.pressure_helper --samples 3 --interval 1`
- Example sane readings:
  - about `969.35 hPa`
  - about `29.7 C`

DHT11 temperature/humidity sensor:
- Data pin is connected to GPIO17 / physical pin 11.
- Uses PyPI package `adafruit-circuitpython-dht`, imported as `adafruit_dht`.
- The helper reads `adafruit_dht.DHT11(board.D17, use_pulseio=False)`.
- DHT11 reads are timing-sensitive and can fail intermittently.
- `humidity_helper.py` defaults to one immediate read attempt for fast feedback; use `--retries 3 --retry-delay 2` for a more patient read.
- Run:
  - `python -m code.helper.humidity_helper`
- See `PINMAP.md` for the current hardware map.
- Verified on the Pi after installing `build-essential`, `python3-dev`, and `adafruit-circuitpython-dht`:
  - about `27-28 C`
  - about `30-48%` humidity during the test

4x4 matrix keypad:
- Uses PyPI package `adafruit-circuitpython-matrixkeypad`, imported as `adafruit_matrixkeypad`.
- Key layout:
  - `1 2 3 A`
  - `4 5 6 B`
  - `7 8 9 C`
  - `* 0 # D`
- Row wiring, top to bottom:
  - physical pin 13 / GPIO27
  - physical pin 15 / GPIO22
  - physical pin 16 / GPIO23
  - physical pin 18 / GPIO24
- Column wiring, left to right:
  - physical pin 32 / GPIO12
  - physical pin 37 / GPIO26
  - physical pin 35 / GPIO19
  - physical pin 36 / GPIO16
- Run:
  - `python -m code.helper.keypad_helper`
- The helper debounces by requiring a stable key state before printing, and only prints one event per held key.

GPIO/pin scanning:
- Use `scripts/gpio_free_pins.py` to detect currently free GPIO header pins.
- The script has a static Raspberry Pi 40-pin mapping and overlays live state from:
  - `gpioinfo`, when available
  - `raspi-gpio get`, when available
  - `pinctrl get`, when available
- Run on the Pi:
  - `python3 scripts/gpio_free_pins.py`
  - `python3 scripts/gpio_free_pins.py --free-only`
  - `python3 scripts/gpio_free_pins.py --json`
- GPIO0/GPIO1 are always marked reserved because they are ID EEPROM pins.
- Treat I2C, SPI, UART, PWM, PCM, Argon, and Stratux consumers as unavailable when the scan reports them as used or in alternate function.
- Last verified on the Pi at `pi@192.168.10.1`.
- Header GPIOs reserved by the project wiring:
  - GPIO2/GPIO3 for BMP581 I2C
  - GPIO17 for DHT11 data
  - GPIO27/GPIO22/GPIO23/GPIO24 for keypad rows
  - GPIO12/GPIO26/GPIO19/GPIO16 for keypad columns
- Currently unavailable examples from the scan:
  - GPIO2/GPIO3 are I2C for the BMP581 path.
  - GPIO4 is consumed by `argon`.
  - GPIO7-GPIO11 are SPI0.
  - GPIO14/GPIO15 are UART.
  - GPIO18 is PWM0.
  - GPIO5/GPIO6 are consumed by regulator lines on this image.

Runtime framework:
- Run:
  - `python -m code.runtime.main`
- Boot service:
  - setup installs and enables `spaceavoider.service`
  - service command runs `.venv/bin/python -m code.runtime.main` in the project directory
  - start manually with `sudo systemctl start spaceavoider.service`
  - stop with `sudo systemctl stop spaceavoider.service`
  - follow logs with `journalctl -u spaceavoider.service -f`
- Useful smoke test:
  - `python -m code.runtime.main --seconds 10 --status-interval 2`
- Thread-safe state is held in `code/runtime/state.py`; do not use `os.environ` for live sensor data.
- Worker schedule:
  - humidity: every 10 seconds, stores humidity only
  - pressure: every 0.5 seconds normally, every 0.1 seconds in approach mode, stores pressure and temperature in an 8-sample ring buffer
  - GPS: every 1 second when available, stores position and GPS altitude
  - METAR: every 5 minutes, instantly overwrites the runtime altimeter setting from latest valid METAR
  - altitude tracker: every 0.5 seconds, pressure-only Kalman placeholder
  - keypad: low-duty debounced polling through Adafruit MatrixKeypad, emits key press events into the runtime queue
- Always assume GPS might be unavailable, stale, wrong, or reporting no fix.
- GPS must never be required for the app to keep running, for approach mode to work, or for callouts to fire.
- GPS is advisory only and currently used as a calibration sanity check when available.
- METAR selection defaults to KCHD if GPS is unavailable; if GPS has a usable fix, try the closest of KCHD/A39 first and fall back to the other station if the selected station has no usable altimeter.
- TODO: Expand METAR selection to any possible METAR station in the USA.
- Runtime console output is mirrored to timestamped files under `log/` at program start.
- Press `C` to start altimeter calibration, enter four digits such as `2992`, press `D` to cancel, and press `*` for backspace.
- Entering calibration mode plays `audio/ai_gen/Calibrate mode.wav` when audio is enabled.
- Successful calibration plays `audio/ai_gen/calibration success.mp3` when audio is enabled.
- Press `A` to enter approach mode; press `A` again to leave approach mode.
- Entering approach mode immediately refreshes METAR and recalculates current altitude/AGL using the current pressure buffer plus latest valid altimeter setting.
- Entering approach mode plays `audio/ai_gen/Approach Mode.wav`.
- Pressing `A` to manually cancel approach mode plays `audio/ai_gen/Approach Mode terminate.wav`; automatic termination does not play the terminate sound.
- Program startup plays `audio/GeoFS-alerts/audio/airbus-autopilot-off.mp3` as the current alive placeholder when audio is enabled.
- Approach mode records the altitude/AGL at entry and estimates AGL as current filtered altitude minus configured known field altitude.
- Approach callouts use `audio/GeoFS-alerts/audio/{threshold}.mp3` for `2500, 1000, 500, 400, 300, 200, 100, 50, 40, 30, 20, 10, 5`.
- Approach callouts are crossing based, not one-shot. A descent from `52` to `38` ft AGL plays/logs `50` then `40`; climbing back to `51` ft AGL plays/logs `50`.
- After the `100` ft callout is reported, approach mode auto-terminates 30 seconds later. After reaching the `100` ft point, climb-back callouts are suppressed, so `50 -> 30 -> 50` does not call out the second `50`.
- Runtime audio playback is interrupting/non-queueing: a new callout stops any current callout and starts immediately.
- Approach callout MP3s are preloaded into `pygame.mixer.Sound` objects at runtime startup; callout playback uses cached decoded clips, not on-demand decoding.
- Entering/leaving approach mode currently prints `BEEP_PLACEHOLDER`.
- Calibration currently uses:
  - known altitude default `1179 ft`
  - moving average pressure
  - moving average BMP581 temperature
  - latest humidity
  - latest GPS altitude as a sanity check only
- Calibration beep/audio actions are placeholders printed as `BEEP_PLACEHOLDER`.

Safety/human factors:
- This is a toy/advisory display only.
- It must not distract during actual flight training.
- ADS-B traffic has coverage, latency, and equipage limitations.
- Baro/GPS callouts are not radio altitude and not certified.
- GPS availability and accuracy are not guaranteed; baro/pressure logic must degrade gracefully without GPS.
- Use a physical mute/off switch before any cockpit use.
- In training flights, joke sounds must not trigger unexpectedly.
- Certified aircraft instruments, CFI, see-and-avoid, and normal procedures remain primary.

Planned features:
1. Traffic display / toy TCAS:
   - Stratux traffic input
   - ownship center
   - range rings
   - traffic symbols
   - relative altitude labels
   - threat color/size
2. Audio callouts:
   - local WAV files
   - pygame mixer playback
   - rate limiting
   - mute button
3. Baro logic:
   - BMP581 pressure
   - relative altitude
   - VSI
   - calibration button
4. Landing/toy Airbus callouts:
   - `100/50/40/30/20`
   - `RETARD RETARD`
   - armed only near runway or by deliberate manual mode
5. Later:
   - METAR/FIS-B integration from Stratux
   - optional temperature/humidity correction for pressure-altitude modeling
   - possible custom Stratux endpoint after live behavior is proven
