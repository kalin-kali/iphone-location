# iphone-location

Spoof the GPS location of your own iPhone (iOS 17+) over USB from Linux, without a Mac or Xcode. Replicates Xcode's "Simulate Location" using [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (a pure-Python reimplementation of Apple's developer/DVT services).

Includes both a CLI (`loc.py`) and a Tkinter GUI (`gui.py`) with saved location presets, GPX route playback with smooth interpolation, drawing routes directly on a map, and place search via OpenStreetMap.

## Requirements

- Linux, Python 3
- An iPhone with **Developer Mode** enabled (Settings → Privacy & Security), connected via a USB **data** cable (not charge-only), with "Trust This Computer" confirmed
- `pkexec` (for the one-time root tunnel needed on iOS 17+)

## Install

```bash
python3 -m venv venv
./venv/bin/pip install pymobiledevice3 gpxpy tkintermapview
```

## Usage

```bash
# CLI
./venv/bin/python3 loc.py add sofia 42.6977 23.3219   # save a preset
./venv/bin/python3 loc.py list                        # list presets
./venv/bin/python3 loc.py set sofia                   # jump to a preset
./venv/bin/python3 loc.py set 42.6977 23.3219          # or raw coordinates
./venv/bin/python3 loc.py status                       # is a simulation active?
./venv/bin/python3 loc.py stop                         # restore real GPS

./venv/bin/python3 loc.py route add my-walk route.gpx  # save a GPX route
./venv/bin/python3 loc.py route list                   # list saved routes
./venv/bin/python3 loc.py route play my-walk           # play it back (smooth by default)
./venv/bin/python3 loc.py route remove my-walk

./venv/bin/python3 loc.py search "Eiffel Tower"        # geocode a place name
./venv/bin/python3 loc.py goto "Eiffel Tower"           # geocode + jump there

# GUI
./venv/bin/python3 gui.py
```

The first `set`/`route play`/`goto` call starts a root `pymobiledevice3 remote tunneld` process via `pkexec` (graphical password prompt) — required for developer commands on iOS 17+.

## How route playback works

`route play` interpolates extra points between your GPX waypoints so movement looks continuous instead of teleporting between them, and paces the interpolation by both time and real-world distance so long legs don't lose resolution. Pass a route through the GUI's smooth/teleport toggle if you want the old jump-between-waypoints behavior instead.
