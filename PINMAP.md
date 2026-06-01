# SpaceAvoider Raspberry Pi Pin Map

Current wiring on the Raspberry Pi 40-pin header.

| Device | Signal | Physical pin | BCM GPIO | Notes |
| --- | --- | ---: | ---: | --- |
| BMP581 pressure sensor | 3V3 | 1 | - | 3.3 V power |
| BMP581 pressure sensor | GND | 6 | - | Ground |
| BMP581 pressure sensor | SDA | 3 | GPIO2 | I2C1 SDA |
| BMP581 pressure sensor | SCL | 5 | GPIO3 | I2C1 SCL |
| DHT11 temp/humidity module | DATA | 11 | GPIO17 | One-wire style DHT data pin |
| 4x4 matrix keypad | ROW 1 | 13 | GPIO27 | Top keypad row: `1 2 3 A` |
| 4x4 matrix keypad | ROW 2 | 15 | GPIO22 | Second keypad row: `4 5 6 B` |
| 4x4 matrix keypad | ROW 3 | 16 | GPIO23 | Third keypad row: `7 8 9 C` |
| 4x4 matrix keypad | ROW 4 | 18 | GPIO24 | Bottom keypad row: `* 0 # D` |
| 4x4 matrix keypad | COL 1 | 32 | GPIO12 | Left keypad column |
| 4x4 matrix keypad | COL 2 | 37 | GPIO26 | Second keypad column |
| 4x4 matrix keypad | COL 3 | 35 | GPIO19 | Third keypad column |
| 4x4 matrix keypad | COL 4 | 36 | GPIO16 | Right keypad column |

Notes:

- Keep GPIO2/GPIO3 reserved for I2C while the BMP581 is connected.
- DHT11 modules usually include the data pull-up resistor. If readings are unreliable, verify the module has a pull-up from DATA to 3.3 V.
- The keypad helper expects rows in physical pin order `13,15,16,18` and columns in physical pin order `32,37,35,36`.
- Re-run `python3 scripts/gpio_free_pins.py --free-only` on the Pi before adding more buttons or sensors.
