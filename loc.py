#!/usr/bin/env python3
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCATIONS_FILE = os.path.join(BASE_DIR, "locations.json")
ROUTES_DIR = os.path.join(BASE_DIR, "routes")
PID_FILE = os.path.join(BASE_DIR, "loc.pid")
STATE_FILE = os.path.join(BASE_DIR, "loc.state")
LOG_FILE = os.path.join(BASE_DIR, "loc.log")
TUNNELD_LOG_FILE = os.path.join(BASE_DIR, "tunneld.log")
PYMOBILEDEVICE3 = os.path.join(BASE_DIR, "venv", "bin", "pymobiledevice3")
TUNNELD_URL = "http://127.0.0.1:49151/"
DENSIFIED_ROUTE_FILE = os.path.join(BASE_DIR, ".densified_route.gpx")
STEP_SECONDS = 1.0  # interpolation granularity so playback moves smoothly instead of teleporting
MAX_STEP_METERS = 20  # cap real-world distance per interpolated step, else long legs still "jump"
MAX_STEPS_PER_SEGMENT = 5000  # ceiling so absurd distances don't explode into huge GPX/log files —
# high enough that realistic drawn/walked legs (tens of km) keep full MAX_STEP_METERS resolution;
# a lower cap here silently coarsens long legs, which is what made routes look smooth in some spots
# and teleport-y in others depending on how far apart two clicked points happened to be
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "iphone-location-tool (personal use)"

os.makedirs(ROUTES_DIR, exist_ok=True)


def load_locations():
    if not os.path.exists(LOCATIONS_FILE):
        return {}
    with open(LOCATIONS_FILE) as f:
        return json.load(f)


def save_locations(locations):
    with open(LOCATIONS_FILE, "w") as f:
        json.dump(locations, f, indent=2, ensure_ascii=False)


def tunneld_running():
    try:
        urllib.request.urlopen(TUNNELD_URL, timeout=2)
        return True
    except Exception:
        return False


def get_running_pid():
    if not os.path.exists(PID_FILE):
        return None
    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        os.remove(PID_FILE)
        return None


def ensure_tunneld():
    if tunneld_running():
        return
    print("tunneld не работи, стартирам го (ще поиска парола за админ права)...")
    log = open(TUNNELD_LOG_FILE, "a")
    subprocess.Popen(
        ["pkexec", PYMOBILEDEVICE3, "remote", "tunneld"],
        stdout=log, stderr=log,
    )
    for _ in range(30):
        if tunneld_running():
            print("tunneld е готов.")
            return
        time.sleep(1)
    print("tunneld не стартира навреме - провери tunneld.log за грешки.")
    sys.exit(1)


def _start_process(cmd, label):
    """Kill any running simulation, start a new one, track its PID + label."""
    ensure_tunneld()

    old_pid = get_running_pid()
    if old_pid:
        os.kill(old_pid, signal.SIGTERM)
        os.remove(PID_FILE)

    log = open(LOG_FILE, "a")
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    with open(STATE_FILE, "w") as f:
        json.dump({"label": label}, f)
    return proc.pid


def do_set(lat, lon):
    """Start (or replace) the location simulation at a fixed point. Returns the new PID."""
    cmd = [PYMOBILEDEVICE3, "developer", "dvt", "simulate-location", "set", "--", str(lat), str(lon)]
    return _start_process(cmd, f"{lat}, {lon}")


def _route_points_with_time(path):
    """List of (lat, lon, datetime) for a GPX file's points. Points missing a
    <time> get synthetic walking-pace timestamps (same logic as save_drawn_route)."""
    import gpxpy

    with open(path) as f:
        gpx = gpxpy.parse(f)
    raw = [(p.latitude, p.longitude, p.time) for trk in gpx.tracks for seg in trk.segments for p in seg.points]
    raw += [(p.latitude, p.longitude, p.time) for rte in gpx.routes for p in rte.points]
    if not raw:
        return []

    result = []
    t = raw[0][2] or datetime.now(timezone.utc)
    prev = None
    for lat, lon, orig_time in raw:
        if orig_time is not None:
            t = orig_time
        elif prev is not None:
            dist = _haversine_m(prev, (lat, lon))
            t += timedelta(seconds=_leg_pace_seconds(dist))
        result.append((lat, lon, t))
        prev = (lat, lon)
    return result


def _densify_points(points_with_time, step_seconds=STEP_SECONDS, max_step_meters=MAX_STEP_METERS):
    """Insert linearly-interpolated points between waypoints so playback moves
    continuously instead of jumping. Step count is driven by whichever needs
    more granularity: elapsed time (step_seconds cadence) or real-world
    distance (max_step_meters per hop) — a long leg squeezed into a short
    capped duration would otherwise still take giant, teleport-looking hops
    even with 1-second timing."""
    if not points_with_time:
        return []
    dense = [points_with_time[0]]
    for (lat1, lon1, t1), (lat2, lon2, t2) in zip(points_with_time, points_with_time[1:]):
        gap = (t2 - t1).total_seconds()
        dist = _haversine_m((lat1, lon1), (lat2, lon2))
        steps_by_time = max(int(gap / step_seconds), 1)
        steps_by_distance = max(int(dist / max_step_meters), 1)
        steps = min(max(steps_by_time, steps_by_distance), MAX_STEPS_PER_SEGMENT)
        for s in range(1, steps + 1):
            frac = s / steps
            dense.append((lat1 + (lat2 - lat1) * frac, lon1 + (lon2 - lon1) * frac, t1 + timedelta(seconds=gap * frac)))
    return dense


def _write_densified_gpx(points_with_time):
    import gpxpy.gpx

    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    for lat, lon, t in points_with_time:
        segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, time=t))
    with open(DENSIFIED_ROUTE_FILE, "w") as f:
        f.write(gpx.to_xml())
    return DENSIFIED_ROUTE_FILE


def do_play_route(path, timing_randomness=0, smooth=True):
    """Start (or replace) GPX route playback. Returns the new PID.

    smooth=True interpolates extra points so movement looks continuous
    instead of jumping between waypoints."""
    play_path = path
    if smooth:
        points_with_time = _route_points_with_time(path)
        dense = _densify_points(points_with_time)
        if len(dense) >= 2:
            play_path = _write_densified_gpx(dense)

    cmd = [PYMOBILEDEVICE3, "developer", "dvt", "simulate-location", "play"]
    if timing_randomness:
        cmd += ["--timing-randomness", str(timing_randomness)]
    cmd.append(play_path)
    pace = "плавно" if smooth else "телепорт"
    return _start_process(cmd, f"маршрут: {os.path.basename(path)} ({pace})")


def do_stop():
    """Stop the location simulation if running. Returns True if something was stopped."""
    pid = get_running_pid()
    if not pid:
        return False
    os.kill(pid, signal.SIGTERM)
    os.remove(PID_FILE)
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    return True


def get_state_label():
    """Return the human-readable label of the active simulation, or None."""
    if not get_running_pid():
        return None
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return json.load(f).get("label")


def list_routes():
    if not os.path.isdir(ROUTES_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(ROUTES_DIR) if f.endswith(".gpx"))


def route_path(name):
    return os.path.join(ROUTES_DIR, f"{name}.gpx")


def resolve_route(name_or_path):
    """Accept either a saved route name or a direct path to a .gpx file."""
    if os.path.isfile(name_or_path):
        return name_or_path
    candidate = route_path(name_or_path)
    if os.path.isfile(candidate):
        return candidate
    return None


def route_points(path):
    """List of (lat, lon) tuples for a GPX file's track/route points."""
    import gpxpy
    with open(path) as f:
        gpx = gpxpy.parse(f)
    points = [(p.latitude, p.longitude) for trk in gpx.tracks for seg in trk.segments for p in seg.points]
    points += [(p.latitude, p.longitude) for rte in gpx.routes for p in rte.points]
    return points


def route_point_count(path):
    """Number of track/route points in a GPX file, or None if it can't be parsed."""
    try:
        return len(route_points(path))
    except Exception:
        return None


def add_route(name, src_path):
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"Файлът '{src_path}' не съществува.")
    if route_point_count(src_path) is None:
        raise ValueError(f"'{src_path}' не изглежда да е валиден GPX файл.")
    shutil.copy(src_path, route_path(name))


WALK_SPEED_MPS = 1.4  # ~5 km/h, average walking pace
FAST_SPEED_MPS = 20  # ~72 km/h, used once walking would take too long — keeps pacing proportional
# to distance instead of flattening every long leg to the same fixed duration. A flat cap made a
# 1km leg and a 130km leg both take exactly MAX_SEGMENT_SECONDS, implying wildly different speeds
# (33 m/s vs 4333 m/s) — when such legs sit next to each other in one route, the interpolation
# density (and therefore the real-world GPS update rate) jumps hugely right at the shared waypoint,
# which looks like small teleports at that corner even though each leg individually is smooth.
MAX_SEGMENT_SECONDS = 30  # walking-pace threshold: beyond this many seconds of walking, switch to FAST_SPEED_MPS
MAX_LEG_SECONDS = 180  # true ceiling so extremely far-apart clicks still don't imply waiting forever


def _leg_pace_seconds(dist_m):
    """Real-world seconds a route leg of dist_m should take: walking pace for short
    legs, scaling up to a faster-but-still-distance-proportional pace for longer
    ones, capped at MAX_LEG_SECONDS so far-apart clicks stay watchable."""
    walk = dist_m / WALK_SPEED_MPS
    if walk <= MAX_SEGMENT_SECONDS:
        return max(walk, 1)
    return min(max(dist_m / FAST_SPEED_MPS, MAX_SEGMENT_SECONDS), MAX_LEG_SECONDS)


def _haversine_m(p1, p2):
    lat1, lon1 = p1
    lat2, lon2 = p2
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def save_drawn_route(name, points, interval_seconds=None):
    """Save a hand-drawn list of (lat, lon) points as a GPX route.

    If interval_seconds is given, every point is spaced by that fixed
    duration. Otherwise timestamps are paced by _leg_pace_seconds (walking
    speed, scaling up to FAST_SPEED_MPS for longer legs) so playback feels
    realistic."""
    import gpxpy.gpx

    if len(points) < 2:
        raise ValueError("Трябват поне 2 точки за маршрут.")

    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

    t = datetime.now(timezone.utc)
    prev = None
    for lat, lon in points:
        if prev is not None:
            if interval_seconds is not None:
                gap = interval_seconds
            else:
                dist = _haversine_m(prev, (lat, lon))
                gap = _leg_pace_seconds(dist)
            t += timedelta(seconds=gap)
        segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, time=t))
        prev = (lat, lon)

    with open(route_path(name), "w") as f:
        f.write(gpx.to_xml())


def remove_route(name):
    path = route_path(name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def search_places(query, limit=5):
    """Look up a place name via OpenStreetMap/Nominatim. Returns a list of
    {"name", "lat", "lon"} dicts, best match first."""
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({
        "q": query, "format": "json", "limit": str(limit),
    })
    req = urllib.request.Request(url, headers={"User-Agent": NOMINATIM_USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        results = json.loads(resp.read().decode())
    return [
        {"name": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])}
        for r in results
    ]


def cmd_set(args):
    if len(args) == 1:
        locations = load_locations()
        name = args[0]
        if name not in locations:
            print(f"Няма запазена локация с име '{name}'.")
            print("Налични:", ", ".join(locations) or "(няма)")
            sys.exit(1)
        lat, lon = locations[name]["lat"], locations[name]["lon"]
    elif len(args) == 2:
        lat, lon = float(args[0]), float(args[1])
    else:
        print("Употреба: loc.py set <име> | loc.py set <lat> <lon>")
        sys.exit(1)

    pid = do_set(lat, lon)
    print(f"Локацията се сменя на {lat}, {lon} ... (PID {pid})")


def cmd_stop(args):
    if do_stop():
        print("Симулацията е спряна, телефонът се връща на истинската GPS локация.")
    else:
        print("Няма активна симулация в момента.")


def cmd_status(args):
    pid = get_running_pid()
    if pid:
        label = get_state_label()
        suffix = f" - {label}" if label else ""
        print(f"Активна симулация (PID {pid}){suffix}.")
    else:
        print("Няма активна симулация - реална GPS локация.")


def cmd_list(args):
    locations = load_locations()
    if not locations:
        print("Няма запазени локации.")
        return
    for name, coords in locations.items():
        print(f"  {name}: {coords['lat']}, {coords['lon']}")


def cmd_add(args):
    if len(args) != 3:
        print("Употреба: loc.py add <име> <lat> <lon>")
        sys.exit(1)
    name, lat, lon = args[0], float(args[1]), float(args[2])
    locations = load_locations()
    locations[name] = {"lat": lat, "lon": lon}
    save_locations(locations)
    print(f"Добавена локация '{name}': {lat}, {lon}")


def cmd_remove(args):
    if len(args) != 1:
        print("Употреба: loc.py remove <име>")
        sys.exit(1)
    name = args[0]
    locations = load_locations()
    if name not in locations:
        print(f"Няма локация с име '{name}'.")
        sys.exit(1)
    del locations[name]
    save_locations(locations)
    print(f"Премахната локация '{name}'.")


def cmd_search(args):
    if not args:
        print("Употреба: loc.py search <текст за търсене>")
        sys.exit(1)
    query = " ".join(args)
    try:
        results = search_places(query)
    except Exception as e:
        print(f"Грешка при търсене: {e}")
        sys.exit(1)
    if not results:
        print("Няма намерени резултати.")
        return
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['name']}  ({r['lat']}, {r['lon']})")
    print("Задай локация с: loc.py set <lat> <lon>")


def cmd_goto(args):
    if not args:
        print("Употреба: loc.py goto <текст за търсене>")
        sys.exit(1)
    query = " ".join(args)
    try:
        results = search_places(query)
    except Exception as e:
        print(f"Грешка при търсене: {e}")
        sys.exit(1)
    if not results:
        print("Няма намерени резултати.")
        sys.exit(1)
    best = results[0]
    pid = do_set(best["lat"], best["lon"])
    print(f"Отивам на: {best['name']} ({best['lat']}, {best['lon']}) ... (PID {pid})")
    if len(results) > 1:
        print("Други намерени съвпадения:")
        for r in results[1:]:
            print(f"  - {r['name']}  ({r['lat']}, {r['lon']})")


def cmd_route(args):
    if not args:
        print("Употреба: loc.py route <play|list|add|remove> [аргументи]")
        sys.exit(1)
    sub, rest = args[0], args[1:]

    if sub == "list":
        routes = list_routes()
        if not routes:
            print("Няма запазени маршрути.")
            return
        for name in routes:
            count = route_point_count(route_path(name))
            info = f"{count} точки" if count is not None else "?"
            print(f"  {name}: {info}")

    elif sub == "play":
        if len(rest) not in (1, 2):
            print("Употреба: loc.py route play <име|път.gpx> [timing-randomness]")
            sys.exit(1)
        path = resolve_route(rest[0])
        if not path:
            print(f"Няма маршрут '{rest[0]}' и не е валиден път до файл.")
            print("Налични:", ", ".join(list_routes()) or "(няма)")
            sys.exit(1)
        randomness = int(rest[1]) if len(rest) == 2 else 0
        pid = do_play_route(path, randomness)
        print(f"Пускам маршрут {os.path.basename(path)} ... (PID {pid})")

    elif sub == "add":
        if len(rest) != 2:
            print("Употреба: loc.py route add <име> <път-до.gpx>")
            sys.exit(1)
        name, src_path = rest
        try:
            add_route(name, src_path)
        except (FileNotFoundError, ValueError) as e:
            print(str(e))
            sys.exit(1)
        print(f"Добавен маршрут '{name}'.")

    elif sub == "remove":
        if len(rest) != 1:
            print("Употреба: loc.py route remove <име>")
            sys.exit(1)
        if remove_route(rest[0]):
            print(f"Премахнат маршрут '{rest[0]}'.")
        else:
            print(f"Няма маршрут с име '{rest[0]}'.")
            sys.exit(1)

    else:
        print("Употреба: loc.py route <play|list|add|remove> [аргументи]")
        sys.exit(1)


COMMANDS = {
    "set": cmd_set,
    "stop": cmd_stop,
    "status": cmd_status,
    "list": cmd_list,
    "add": cmd_add,
    "remove": cmd_remove,
    "route": cmd_route,
    "search": cmd_search,
    "goto": cmd_goto,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Употреба: loc.py <set|stop|status|list|add|remove|route|search|goto> [аргументи]")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
