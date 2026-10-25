#!/usr/bin/env python3

# Test Ortho4XP end to end and offline: build the tile +45+005 from a
# generated DEM, cached OSM data, and imagery served by this script, and
# check what Steps 1 to 3 produce. With -gui, check that the GUI starts
# and keeps running, and drive its windows, instead.

import bz2
import glob
import hashlib
import http.server
import io
import os
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import threading
import time

import numpy
from PIL import Image

usage = "usage: test.py [-gui]"

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "dar" in sys.platform:
    (sys_dir, exe) = ("mac", "")
elif "win" in sys.platform:
    (sys_dir, exe) = ("win", ".exe")
else:
    (sys_dir, exe) = ("lin", "")

(lat, lon) = (45, 5)
tile = "+45+005"
color = (90, 140, 60)

def line(a, b, n = 20):
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]

def box(lat0, lon0, lat1, lon1):
    return [(lat0, lon0), (lat0, lon1), (lat1, lon1), (lat1, lon0), (lat0, lon0)]

# The tile config, and the OSM ways as (lat, lon) lists with their tags.
# The coastline runs along lat + lon = 50.25 with the sea to the south
# west, and the ZL 14 zone straddles it. The airport lies on the plateau
# of the DEM, and the motorway climbs the steep flank of its hill, which
# makes Step 1 level it.
config = {
    "default_website": "TEST",
    "default_zl": 12,
    "zone_list": [([x for p in box(45.05, 5.05, 45.2, 5.2) for x in p], 14, "TEST")],
    "mesh_zl": 16,
    "custom_dem": os.path.join("Elevation_data", "test.hgt"),
    "road_level": 1,
}

osm = {
    "airports": [
        (box(45.74, 5.22, 45.76, 5.28), {"aeroway": "aerodrome", "icao": "LFZZ", "name": "Test"}),
        ([(45.75, 5.23), (45.75, 5.27)], {"aeroway": "runway", "width": "45"}),
    ],
    "big_roads": [(line((45.3, 5.47), (45.7, 5.97)), {"highway": "motorway"})],
    "coastline": [(line((45.3, 4.95), (44.95, 5.3)), {"natural": "coastline"})],
    "water": [(box(45.84, 5.6, 45.852, 5.616), {"natural": "water", "name": "Test"})],
}

# Settings must never run code. The tests put this one in config files and
# fields, and require that no file named pwned appears.
payload = '__import__("pathlib").Path("pwned").touch()'

# A tile config in the form of 1.20, which appends zones one per line.
legacy_code = """
import ast
import O4_Config_Utils as CFG
tile = CFG.Tile(45, 5, "")
tile.read_from_config(sys.argv[1])
if tile.zone_list != ast.literal_eval(sys.argv[2]):
    sys.exit("zones " + repr(tile.zone_list) + ", want " + sys.argv[2])
"""

# The GUI, driven in-process.
gui_code = """
import os
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_GUI_Utils as GUI
import O4_Config_Utils as CFG
import O4_Tile_Utils as TILE
errors = []
gui = GUI.Ortho4XP_GUI()
gui.open_config_window()
gui.config_window.v_["skip_downloads"].set(sys.argv[1])
gui.config_window.v_["masks_width"].set("[1, 2")
gui.config_window.apply_changes()
if TILE.skip_downloads is not False or CFG.masks_width != 100:
    errors.append("config window: kept invalid values")
gui.update()
gui.exit_prg()
if os.path.exists("pwned"):
    errors.append("config window: a setting ran code")
if errors:
    sys.exit("\\n".join(errors))
"""

skip = {
    ".git", ".last_gui_params.txt", "Elevation_data", "Geotiffs", "Masks",
    "OSM_data", "Ortho4XP.cfg", "Ortho4XP.log", "Orthophotos", "Previews",
    "python", "Tiles", "tmp", "yOrtho4XP_Overlays",
}

def ignore(dir, names):
    if os.path.samefile(dir, root_dir):
        return [name for name in names if name in skip]
    if os.path.samefile(dir, os.path.join(root_dir, "Utils")):
        return [name for name in names if name == "build"]
    return [name for name in names if name == "__pycache__"]

def write_dem(path):
    # 1201 rows from north to south, and columns from west to east: sea,
    # a coastal slope up to a 400 m plateau, and an 800 m hill.
    (lons, lats) = numpy.meshgrid(lon + numpy.arange(1201) / 1200, lat + 1 - numpy.arange(1201) / 1200)
    inland = lats + lons - 50.25
    alt = numpy.clip(2000 * inland, 0, 400)
    alt += 800 * numpy.exp(-((lats - 45.5) ** 2 + (lons - 5.7) ** 2) / 0.03 ** 2)
    alt[inland <= 0] = 0
    alt.astype(">i2").tofile(path)

def write_osm(path, ways):
    nodes = {}
    (head, body) = ([], [])
    for (i, (coords, tags)) in enumerate(ways, 1):
        body.append('  <way id="' + str(i) + '" version="1">')
        for c in coords:
            if c not in nodes:
                nodes[c] = len(nodes) + 1
                head.append('  <node id="{}" lat="{:.7f}" lon="{:.7f}" version="1"/>'.format(nodes[c], c[0], c[1]))
            body.append('    <nd ref="' + str(nodes[c]) + '"/>')
        for (k, v) in tags.items():
            body.append('    <tag k="' + k + '" v="' + v + '"/>')
        body.append("  </way>")
    with bz2.open(path, "wt", encoding = "utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="Ortho4XP">\n')
        f.write("\n".join(head + body) + "\n</osm>\n")

def serve(jpeg):
    paths = []
    agents = set()
    lock = threading.Lock()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                paths.append(self.path)
                agents.add(self.headers.get("User-Agent"))
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target = server.serve_forever, daemon = True).start()
    return (server, paths, agents)

# A request that leaves this machine fails at once.
def offline_env():
    env = {k: v for (k, v) in os.environ.items() if not k.lower().endswith("_proxy")}
    names = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]
    if os.name != "nt":
        names += [name.lower() for name in names]
    for name in names:
        env[name] = "127.0.0.1,localhost" if name.lower() == "no_proxy" else "http://127.0.0.1:9"
    return env

def run(cmd, cwd, log, timeout):
    with open(log, "ab") as f:
        try:
            return subprocess.run(cmd, cwd = cwd, env = offline_env(), stdout = f, stderr = subprocess.STDOUT, timeout = timeout).returncode
        except subprocess.TimeoutExpired:
            return "timeout"

# Runs Python code in the copied tree, with the program's modules on the
# path. The code reports failures with sys.exit(message), and check
# returns them as errors.
def check(name, code, args, work, top, log, timeout = 300):
    path = os.path.join(work, name + ".py")
    with open(path, "w") as f:
        f.write("import sys\nsys.dont_write_bytecode = True\nsys.path[:0] = ['src', 'Providers']\n" + code)
    with open(log, "ab") as out:
        try:
            r = subprocess.run([sys.executable, path] + args, cwd = top, env = offline_env(), stdout = out, stderr = subprocess.PIPE, timeout = timeout)
            (status, err) = (r.returncode, r.stderr.decode("utf-8", "replace"))
        except subprocess.TimeoutExpired:
            (status, err) = ("timeout", "")
        out.write(err.encode("utf-8"))
    if status == 0:
        return []
    return [name + ": " + line for line in err.splitlines() if line.strip()] or [name + ": exit status " + str(status) + ", see " + log]

def test_tile(work, top):
    image = Image.new("RGB", (256, 256), color)
    f = io.BytesIO()
    image.save(f, "JPEG", quality = 95)
    jpeg = f.getvalue()
    served = numpy.asarray(Image.open(io.BytesIO(jpeg)), dtype = float).mean(axis = (0, 1))
    (server, paths, agents) = serve(jpeg)

    os.makedirs(os.path.join(top, "Elevation_data"))
    write_dem(os.path.join(top, config["custom_dem"]))
    osm_dir = os.path.join(top, "OSM_data", "+40+000", tile)
    os.makedirs(osm_dir)
    for (suffix, ways) in osm.items():
        write_osm(os.path.join(osm_dir, tile + "_" + suffix + ".osm.bz2"), ways)
    os.makedirs(os.path.join(top, "Providers", "Test"))
    with open(os.path.join(top, "Providers", "Test", "TEST.lay"), "w") as f:
        f.write("grid_type=webmercator\n")
        f.write("url_template=http://127.0.0.1:" + str(server.server_port) + "/{zoom}/{x}/{y}.jpg\n")
        f.write("in_GUI=False\n")
        f.write("fake_headers={'User-Agent': user_agent_ortho4xp}\n")
    with open(os.path.join(top, "Providers", "Test", "EVIL.lay"), "w") as f:
        f.write("url_template=http://127.0.0.1:9/{zoom}/{x}/{y}.jpg\n")
        f.write("in_GUI=" + payload + "\n")
        f.write("fake_headers={'User-Agent': " + payload + "}\n")
    build = os.path.join(top, "Tiles", "zOrtho4XP_" + tile)
    os.makedirs(build)
    with open(os.path.join(build, "Ortho4XP_" + tile + ".cfg"), "w") as f:
        for (k, v) in config.items():
            f.write(k + "=" + str(v) + "\n")
    with open(os.path.join(top, "Ortho4XP.cfg"), "w") as f:
        f.write("skip_downloads=" + payload + "\n")

    log = os.path.join(work, "Ortho4XP.log")
    status = run([sys.executable, "Ortho4XP.py", str(lat), str(lon)], top, log, 1800)
    server.shutdown()
    if status != 0:
        return ["Ortho4XP.py " + str(lat) + " " + str(lon) + ": exit status " + str(status) + ", see " + log]

    errors = []
    with open(log, encoding = "utf-8", errors = "replace") as f:
        out = f.read()
    if "invalid line: skip_downloads=" not in out:
        errors.append("Ortho4XP.cfg: no warning for a setting that holds code")
    if "fake headers for provider EVIL not valid" not in out:
        errors.append("EVIL.lay: no warning for headers that hold code")
    legacy = os.path.join(work, "legacy.cfg")
    with open(legacy, "w") as f:
        for zone in config["zone_list"] + [payload]:
            f.write("zone_list.append(" + str(zone) + ")\n")
    errors += check("legacy", legacy_code, [legacy, repr(config["zone_list"])], work, top, log)
    if os.path.exists(os.path.join(top, "pwned")):
        errors.append("a config or provider file ran code")
    textures = sorted(glob.glob(os.path.join(build, "textures", "*.dds")))
    formats = {}
    for path in textures:
        with open(path, "rb") as f:
            formats.setdefault(f.read(88)[84:].decode("latin-1"), []).append(path)
    if sorted(formats) != ["DXT1", "DXT5"]:
        errors.append("textures: formats " + str(sorted(formats)) + ", want DXT1 and DXT5")
    if len(paths) != 256 * len(textures) or len(set(paths)) != len(paths):
        errors.append("server: " + str(len(paths)) + " requests (" + str(len(set(paths))) + " distinct) for " + str(len(textures)) + " textures, want 256 each")
    if any(not str(agent).startswith("Ortho4XP/") for agent in agents):
        errors.append("server: user agents " + str(sorted(map(str, agents))) + ", want Ortho4XP/")
    for path in formats.get("DXT1", [])[:1]:
        mean = numpy.asarray(Image.open(path).convert("RGB"), dtype = float).mean(axis = (0, 1))
        if numpy.abs(mean - served).max() > 6:
            errors.append(path + ": mean color " + str(mean.round(1)) + ", want " + str(served.round(1)))
    if not glob.glob(os.path.join(top, "Masks", "+40+000", tile, "*.png")):
        errors.append("masks: none")
    if not glob.glob(os.path.join(build, "terrain", "*.ter")):
        errors.append("terrain: no .ter files")

    dsf = os.path.join(build, "Earth nav data", "+40+000", tile + ".dsf")
    with open(dsf, "rb") as f:
        data = f.read()
    if data[:8] != b"XPLNEDSF" or hashlib.md5(data[:-16]).digest() != data[-16:]:
        errors.append(dsf + ": bad header or checksum")
    text = os.path.join(work, "dsf.txt")
    status = run([os.path.join(top, "Utils", sys_dir, "DSFTool" + exe), "-dsf2text", dsf, text], work, log, 300)
    if status != 0 or not os.path.exists(text):
        errors.append("DSFTool -dsf2text: exit status " + str(status))
    else:
        with open(text, encoding = "latin-1") as f:
            if "PROPERTY sim/west 5" not in f.read():
                errors.append(text + ": missing sim/west 5")

    mesh = os.path.join(work, "test.mesh")
    shutil.copy(os.path.join(build, "Data" + tile + ".mesh"), mesh)
    status = run([os.path.join(top, "Utils", sys_dir, "moulinette" + exe), "12", mesh], work, log, 300)
    if status != 0:
        errors.append("moulinette: exit status " + str(status))

    poly = os.path.join(work, "square.poly")
    with open(poly, "w") as f:
        f.write("4 2 0 0\n1 0 0\n2 1 0\n3 1 1\n4 0 1\n4 0\n1 1 2\n2 2 3\n3 3 4\n4 4 1\n0\n0\n")
    status = run([os.path.join(top, "Utils", sys_dir, "triangle" + exe), "-pAYPQ", poly], work, log, 300)
    if status != 0 or not all(os.path.exists(os.path.join(work, "square.1." + ext)) for ext in ("node", "ele")):
        errors.append("triangle -pAYPQ: exit status " + str(status))
    return errors

def test_gui(work, top):
    import tkinter
    from PIL import ImageTk
    try:
        tk = tkinter.Tk()
        photo = ImageTk.PhotoImage(Image.new("RGB", (16, 16), color))
        tk.destroy()
    except Exception as e:
        return ["Tk: " + repr(e)]
    log = os.path.join(work, "Ortho4XP.log")
    with open(log, "wb") as f:
        gui = subprocess.Popen([sys.executable, "Ortho4XP.py"], cwd = top, env = offline_env(), stdout = f, stderr = subprocess.STDOUT)
        time.sleep(15)
        status = gui.poll()
        gui.kill()
        gui.wait()
    errors = []
    if status is not None:
        errors.append("GUI exited with status " + str(status))
    with open(log, encoding = "utf-8", errors = "replace") as f:
        out = f.read()
    for text in ("Traceback", "Exception in Tkinter callback"):
        if text in out:
            errors.append("GUI printed " + text + ", see " + log)
    return errors + check("gui", gui_code, [payload], work, top, log)

if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["-gui"]):
        print(usage, file = sys.stderr)
        sys.exit(2)
    work = tempfile.mkdtemp(prefix = "Ortho4XP-test-", dir = os.path.dirname(root_dir))
    top = os.path.join(work, "Ortho4XP")
    shutil.copytree(root_dir, top, ignore = ignore)
    errors = test_gui(work, top) if sys.argv[1:] else test_tile(work, top)
    if errors:
        for error in errors:
            print("test.py: " + error, file = sys.stderr)
        print("test.py: files kept in " + work, file = sys.stderr)
        sys.exit(1)
    shutil.rmtree(work)
