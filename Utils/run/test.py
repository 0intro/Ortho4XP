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
import math
import os
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import threading
import time
import traceback

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
# west, and the ZL 14 zone straddles it. The zone takes its imagery from
# TEST2, which sends the generic request headers, and the rest of the tile
# from TEST, which sends headers of its own. The airport lies on the
# plateau of the DEM and gets ZL 15 textures, and the motorway climbs the
# steep flank of its hill, which makes Step 1 level it.
config = {
    "default_website": "TEST",
    "default_zl": 12,
    "zone_list": [([x for p in box(45.05, 5.05, 45.2, 5.2) for x in p], 14, "TEST2")],
    "mesh_zl": 16,
    "custom_dem": os.path.join("Elevation_data", "test.hgt"),
    "road_level": 1,
    "cover_airports_with_highres": "True",
    "cover_zl": 15,
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

# Patches of the tile as OSM ways, by name: a ramp on the plateau that
# falls from 450 m at its west end to 420 m at its east end, and two that
# Step 1 must reject: a ramp whose way ends 8 m east of its start, and one
# with cells of zero size.
ramp = box(45.9, 5.4, 45.9006, 5.42)
patches = {
    "ramp": [(ramp, {"altitude_high": "450", "altitude_low": "420"})],
    "open": [(box(45.95, 5.45, 45.9506, 5.47)[:-1] + [(45.95, 5.4501)], {"altitude_high": "450", "altitude_low": "420"})],
    "zero": [(box(45.96, 5.5, 45.9606, 5.52), {"altitude_high": "450", "altitude_low": "420", "cell_size": "0"})],
}

# Settings must never run code. The tests put this one in config files and
# fields, and require that no file named pwned appears. The second needs no
# builtins and gives a list, so it also gets past an eval without them.
payload = '__import__("pathlib").Path("pwned").touch()'
payload_list = "().__class__.__base__.__subclasses__()"

# A tile config in the form of 1.20, which appends zones one per line,
# holds a bool edited by hand to 1, and hmin, a setting of 1.30 that only
# verbosity 2 reports, and the global config, which Python before 3.15
# wrote in cp1252 on Windows.
legacy_code = """
import ast
import contextlib
import io
import O4_Config_Utils as CFG
tile = CFG.Tile(45, 5, "")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    tile.read_from_config(sys.argv[1])
if "hmin" in out.getvalue():
    sys.exit("hmin reported at verbosity 1: " + out.getvalue())
if tile.zone_list != ast.literal_eval(sys.argv[2]):
    sys.exit("zones " + repr(tile.zone_list) + ", want " + sys.argv[2])
if tile.use_masks_for_inland is not True:
    sys.exit("use_masks_for_inland=1 read as " + repr(tile.use_masks_for_inland) + ", want True")
if CFG.custom_scenery_dir != sys.argv[3]:
    sys.exit("custom_scenery_dir " + repr(CFG.custom_scenery_dir) + ", want " + repr(sys.argv[3]))
"""

scenery_dir = "C:\\Users\\Frédéric\\X-Plane 12 €\\Custom Scenery"

# 7-Zip as Ortho4XP finds it, and on Windows, the 7-Zip installed in
# Program Files when the one in Utils is missing. A copy in Utils that
# cannot run is passed over, and a 7-Zip that fails to start gets an
# error.
seven_zip_code = """
import os
import subprocess
import O4_Overlay_Utils as OVL
cmd = OVL.seven_zip()
if not cmd:
    sys.exit("7-Zip not found")
with open("7z.txt", "w") as f:
    f.write("Ortho4XP")
subprocess.run([cmd, "a", "7z.7z", "7z.txt"], stdout = subprocess.DEVNULL, check = True)
os.remove("7z.txt")
if not OVL.extract_7z("7z.7z", "tmp"):
    sys.exit(cmd + ": could not extract 7z.7z")
with open(os.path.join("tmp", "7z.txt")) as f:
    if f.read() != "Ortho4XP":
        sys.exit(cmd + ": extracted the wrong data")
installed = os.path.join(os.environ.get("ProgramFiles", ""), "7-Zip", "7z.exe")
if sys.platform == "win32" and cmd == OVL.seven_zip_bundled and os.path.isfile(installed):
    os.rename(cmd, cmd + ".hidden")
    found = OVL.seven_zip()
    os.rename(cmd + ".hidden", cmd)
    if found != installed:
        sys.exit("7-Zip: found " + str(found) + " without Utils, want " + installed)
if os.name != "nt":
    bundled = OVL.seven_zip_bundled
    made = not os.path.exists(bundled)
    if made:
        open(bundled, "w").close()
    mode = os.stat(bundled).st_mode
    os.chmod(bundled, mode & ~0o111)
    found = OVL.seven_zip()
    if made:
        os.remove(bundled)
    else:
        os.chmod(bundled, mode)
    if found == bundled:
        sys.exit(bundled + ": found while it cannot run")
with open("not7z.txt", "w") as f:
    f.write("Ortho4XP")
OVL.seven_zip = lambda: os.path.abspath("not7z.txt")
if OVL.extract_7z("7z.7z", "tmp"):
    sys.exit("extract_7z: success with a text file for 7-Zip")
"""

# write_geotiff writes alt, rows from north to south, as a GeoTIFF of the
# tile: the corner pixels centered on the tile corners when point is true,
# as in SRTM, and pixels tiling the tile otherwise, as in ALOS.
geotiff_writer = """
import numpy
from PIL import Image, TiffImagePlugin, TiffTags
(lat, lon) = (45, 5)
def write_geotiff(path, alt, point, nodata, compression):
    step = 1 / (alt.shape[0] - 1 if point else alt.shape[0])
    raster_type = 2 if point else 1
    tags = {
        33550: ((step, step, 0.0), TiffTags.DOUBLE),
        33922: ((0.0, 0.0, 0.0, lon, lat + 1, 0.0), TiffTags.DOUBLE),
        34735: ((1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, raster_type, 2048, 0, 1, 4326), TiffTags.SHORT),
        42113: (str(nodata), TiffTags.ASCII),
    }
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    for (tag, (value, kind)) in tags.items():
        ifd[tag] = value
        ifd.tagtype[tag] = kind
    if alt.dtype in (numpy.int8, numpy.int16):
        # Pillow writes 8 and 16-bit samples unsigned unless SampleFormat
        # says otherwise.
        alt = alt.view(alt.dtype.str.replace("i", "u"))
        ifd[339] = 2
    Image.fromarray(alt).save(path, tiffinfo = ifd, compression = compression)
"""

# GeoTIFF elevation, which Pillow reads when GDAL is missing: the test DEM
# as SRTM ships its tiles (int16, Deflate), and as ALOS does (LZW, here in
# float32), each with nodata in its northwest corner. Cells of NaN read as
# nodata. A TIFF without georeference, and one of signed bytes, which
# Pillow misreads, read as zero altitude.
geotiff_code = geotiff_writer + """
import O4_DEM_Utils as DEM
DEM.has_gdal = False
srtm = numpy.fromfile(sys.argv[1], ">i2").reshape(1201, 1201).astype(numpy.int16)
srtm[:10, :10] = -32768
alos = srtm[:1200, :1200].astype(numpy.float32) + 0.25
alos[:10, :10] = -9999
alos[-10:, -10:] = numpy.nan
e = 1 / 2400
cases = [
    ("srtm.tif", srtm, True, -32768, "tiff_deflate", (0, 0, 1, 1)),
    ("alos.tif", alos, False, -9999, "tiff_lzw", (e, e, 1 - e, 1 - e)),
]
for (path, alt, point, nodata, compression, corners) in cases:
    write_geotiff(path, alt, point, nodata, compression)
    want = alt.astype(numpy.float32)
    want[(want == nodata) | numpy.isnan(want)] = -32768
    for info_only in (True, False):
        (epsg, x0, y0, x1, y1, nd, nx, ny, got) = DEM.read_elevation_from_file(path, lat, lon, info_only)
        if (epsg, nd, nx, ny) != (4326, -32768, alt.shape[1], alt.shape[0]):
            sys.exit(path + ": epsg, nodata, and size " + str((epsg, nd, nx, ny)) + ", want " + str((4326, -32768, alt.shape[1], alt.shape[0])))
        if not numpy.allclose((x0, y0, x1, y1), corners, rtol = 0, atol = 1e-9):
            sys.exit(path + ": corners " + str((x0, y0, x1, y1)) + ", want " + str(corners))
        if not info_only and not numpy.array_equal(got, want):
            sys.exit(path + ": the elevations differ from the written ones")
Image.fromarray(alos).save("plain.tif")
write_geotiff("bytes.tif", numpy.arange(-60, 61, dtype = numpy.int8).repeat(121).reshape(121, 121), True, -128, None)
for path in ("plain.tif", "bytes.tif"):
    got = DEM.read_elevation_from_file(path, lat, lon)[-1]
    if got.shape != (3601, 3601) or got.any():
        sys.exit(path + ": read, want zero altitude")
"""

# serve serves files, a dict of bytes by path, whatever the query string,
# and answers 404 for other paths. It returns the URL of the server, and
# the paths and the user agents of the requests, which check_agents
# requires to name Ortho4XP.
file_server = """
import http.server
import threading
def serve(files):
    (paths, agents) = ([], set())
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            agents.add(self.headers.get("User-Agent"))
            body = files.get(self.path.split("?")[0])
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target = server.serve_forever, daemon = True).start()
    return ("http://127.0.0.1:" + str(server.server_port) + "/", paths, agents)
def check_agents(agents):
    if any(not str(agent).startswith("Ortho4XP/") for agent in agents):
        sys.exit("user agents " + str(sorted(map(str, agents))) + ", want Ortho4XP/")
"""

# SRTM and ALOS tiles as OpenTopography serves them, served here: the DEM
# of the tile holds the served tile, a neighbor of sea (404) reads as zero
# altitude, and so does the SRTM tile north, for which the server sends
# the page of a captive portal, which stays out of Elevation_data. A
# downloaded tile serves again from Elevation_data, an SRTM .hgt placed by
# hand goes before the download, and a missing tile gets a warning when it
# is the tile of the build. The labels of 1.40 name the sources too.
opentopography_code = geotiff_writer + file_server + """
import contextlib
import io
import os
import O4_DEM_Utils as DEM
import O4_File_Names as FNAMES
n = numpy.arange(3601, dtype = numpy.int16)
srtm = numpy.add.outer(n, 2 * n)
alos = numpy.add.outer(3 * n[:3600], n[:3600])
srtm_path = "/SRTM_GL1/SRTM_GL1_srtm/N45E005.tif"
alos_path = "/AW3D30/AW3D30_global/ALPSMLC30_N045E005_DSM.tif"
served = {}
for (path, alt, point, compression) in ((srtm_path, srtm, True, "tiff_deflate"), (alos_path, alos, False, "tiff_lzw")):
    name = os.path.basename(path)
    write_geotiff(name, alt, point, -32768, compression)
    with open(name, "rb") as f:
        served[path] = f.read()
served["/SRTM_GL1/SRTM_GL1_srtm/N46E005.tif"] = b"<html><body>Log in to the network</body></html>"
(DEM.opentopography_url, paths, agents) = serve(served)
hgt = FNAMES.base_file_name(lat - 1, lon) + "_SRTMv3.hgt"
os.makedirs(os.path.dirname(hgt), exist_ok = True)
numpy.full((3601, 3601), 7, ">i2").tofile(hgt)
sources = [
    (DEM.available_sources[3], srtm),
    ("SRTMv3 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD", srtm),
    ("ALOS 3W30 (from OpenTopography) - NOW REQUIRES MANUAL DOWNLOAD", alos),
]
for (label, want) in sources:
    alt = DEM.DEM(lat, lon, label).alt_dem
    if not numpy.array_equal(alt[36:-36, 36:-36], want):
        sys.exit(label + ": the tile differs from the served one")
    if alt[:36, 36:-36].any():
        sys.exit(label + ": the tile north (404) holds altitude, want zero")
    if want is srtm and (alt[-36:, 36:-36] != 7).any():
        sys.exit(label + ": the tile south lost the altitude of its .hgt")
for path in (srtm_path, alos_path):
    if paths.count(path) != 1:
        sys.exit(path + ": " + str(paths.count(path)) + " requests, want 1")
if any("N44E005" in path for path in paths):
    sys.exit("requested the tile south, which has an .hgt")
portal = FNAMES.elevation_data("SRTM", lat + 1, lon)
if os.path.exists(portal):
    sys.exit(portal + ": saved the page of a captive portal")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = DEM.ensure_elevation("SRTM", lat + 2, lon, True)
if got or "WARNING: No SRTM tile" not in out.getvalue():
    sys.exit("a missing tile of the build: " + str(got) + ", " + repr(out.getvalue()) + ", want 0 and a warning")
check_agents(agents)
"""

# The mesh of the tile from a community server, served here in a 7z
# archive.
community_code = file_server + """
import os
import subprocess
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mesh_Utils as MESH
import O4_Overlay_Utils as OVL
tile = CFG.Tile(45, 5, "")
mesh = FNAMES.mesh_file(tile.build_dir, tile.lat, tile.lon)
with open(mesh, "rb") as f:
    want = f.read()
subprocess.run([OVL.seven_zip(), "a", "mesh.7z", mesh], stdout = subprocess.DEVNULL, check = True)
with open("mesh.7z", "rb") as f:
    served = {"/" + os.path.basename(mesh) + ".7z": f.read()}
os.remove(mesh)
(MESH.community_prefix, paths, agents) = serve(served)
MESH.community_server = True
# The checks after this one read the mesh, which a failure here restores.
try:
    if not MESH.community_mesh(tile):
        sys.exit("community_mesh failed")
    with open(mesh, "rb") as f:
        got = f.read()
finally:
    with open(mesh, "wb") as f:
        f.write(want)
if got != want:
    sys.exit(mesh + ": differs from the served one")
check_agents(agents)
"""

# Runs the script named by its first argument, with the rest as arguments,
# under a German locale, which groups the thousands of {:n} with periods.
# The first locale that this system has wins. Tcl sets the numeric locale
# to C when tkinter loads, so tkinter loads first.
german_code = """
import locale
import runpy
import sys
import tkinter
for name in ("de_DE.UTF-8", "German_Germany.1252"):
    try:
        print("locale", locale.setlocale(locale.LC_ALL, name), flush = True)
        break
    except locale.Error:
        pass
else:
    sys.exit("german.py: no German locale: install glibc-langpack-de on Fedora, or run locale-gen de_DE.UTF-8 on Debian and Ubuntu")
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name = "__main__")
"""

# The extent tool in Extents/Test, on the cached OSM data of a square, in
# EPSG 4326 and 2154, and on a relation from an Overpass server here,
# whose tags must all reach the cache, then with a grid argument, and with
# one that holds code, which the tool must refuse. extent runs the tool as
# its command line would, and bounds reads the mask_bounds of an .ext file.
extent_code = file_server + """
import bz2
import os
import runpy
from PIL import Image
import O4_Geo_Utils as GEO
payload = sys.argv[1]
os.makedirs(os.path.join("Extents", "Test"))
os.chdir(os.path.join("Extents", "Test"))
tool = os.path.join("..", "..", "src", "O4_Mask_Utils.py")
square = [(45.3, 5.3), (45.3, 5.31), (45.31, 5.31), (45.31, 5.3)]
with bz2.open("Test.osm.bz2", "wt", encoding = "utf-8") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\\n<osm version="0.6">\\n')
    for (i, (lat, lon)) in enumerate(square, 1):
        f.write('  <node id="' + str(i) + '" lat="' + str(lat) + '" lon="' + str(lon) + '"/>\\n')
    f.write('  <way id="1">\\n' + "".join('    <nd ref="' + str(i) + '"/>\\n' for i in (1, 2, 3, 4, 1)) + '  </way>\\n</osm>\\n')
def extent(*args):
    sys.argv = [tool] + list(args)
    try:
        runpy.run_path(tool, run_name = "__main__")
    except SystemExit as e:
        return e.code
    return 0
def bounds(name):
    with open(name + ".ext") as f:
        for line in f:
            if line.startswith("mask_bounds="):
                return [float(x) for x in line[len("mask_bounds="):].split(",")]
    sys.exit(name + ".ext: no mask_bounds")
def check(name, inside, grid):
    (xmin, ymin, xmax, ymax) = bounds(name)
    (x0, y0, x1, y1) = inside
    if not (xmin < x0 and ymin < y0 and xmax > x1 and ymax > y1):
        sys.exit(name + ".ext: mask_bounds " + str((xmin, ymin, xmax, ymax)) + " around " + str(inside))
    if any(abs(b / grid - round(b / grid)) > 1e-6 for b in (xmin, ymin, xmax, ymax)):
        sys.exit(name + ".ext: mask_bounds " + str((xmin, ymin, xmax, ymax)) + " off the grid of " + str(grid))
    with Image.open(name + ".png") as im:
        if not im.getbbox():
            sys.exit(name + ".png: empty")
extent("Test", "100", "0", "200")
check("Test", (5.3, 45.3, 5.31, 45.31), 0.02)
extent("Test", "100", "0", "200", "way[name=Test]", "2154")
GEO.record_epsg(2154)
(x0, y0) = GEO.transform(4326, 2154, 5.3, 45.3)
(x1, y1) = GEO.transform(4326, 2154, 5.31, 45.31)
check("Test_2154", (x0, y0, x1, y1), 2000)
import O4_OSM_Utils as OSM
overpass = "\\n".join([
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<osm version="0.6" generator="Overpass API">',
    '  <node id="11" lat="45.4" lon="5.4"/>',
    '  <node id="12" lat="45.4" lon="5.41"/>',
    '  <node id="13" lat="45.41" lon="5.41"/>',
    '  <node id="14" lat="45.41" lon="5.4"/>',
    '  <way id="21">',
    '    <nd ref="11"/>',
    '    <nd ref="12"/>',
    '    <nd ref="13"/>',
    '    <nd ref="14"/>',
    '    <nd ref="11"/>',
    '  </way>',
    '  <relation id="31">',
    '    <member type="way" ref="21" role="outer"/>',
    '    <tag k="admin_level" v="8"/>',
    '    <tag k="name" v="Test"/>',
    '    <tag k="type" v="boundary"/>',
    '  </relation>',
    '</osm>',
]) + "\\n"
(url, paths, agents) = serve({"/api/interpreter": overpass.encode()})
OSM.overpass_servers = {"DE": url + "api/interpreter"}
extent("Query", "100", "0", "200", "rel[admin_level=8]")
check("Query", (5.4, 45.4, 5.41, 45.41), 0.02)
with bz2.open("Query.osm.bz2", "rt", encoding = "utf-8") as f:
    if 'k="name" v="Test"' not in f.read():
        sys.exit("Query.osm.bz2: the cache lost the tags of the relation")
check_agents(agents)
extent("Query", "100", "0", "200", "rel[admin_level=8]", "4326", "0.05")
check("Query", (5.4, 45.4, 5.41, 45.41), 0.05)
status = extent("Query", "100", "0", "200", "rel[admin_level=8]", "4326", payload)
if status != 1 or os.path.exists("pwned"):
    sys.exit("a grid argument that holds code: exit status " + str(status) + ", want 1 and no code run")
"""

# The airports of a tile that 1.40 built, pickled in Data+45+005.apt, which
# Steps 2 and 3 ask to build again with Step 1.
apt_code = """
import contextlib
import io
import os
import O4_Airport_Utils as APT
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
tile = CFG.Tile(45, 5, "")
os.replace(FNAMES.apt_file(tile), "apt.json")
open(FNAMES.legacy_apt_file(tile), "wb").close()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = APT.read_airport_bounds(tile)
os.remove(FNAMES.legacy_apt_file(tile))
os.replace("apt.json", FNAMES.apt_file(tile))
if got or "Run Step 1 again" not in out.getvalue():
    sys.exit("airports of 1.40: " + repr(got) + ", " + repr(out.getvalue()) + ", want none and a request for Step 1")
"""

# Links a tile into Custom Scenery as the GUI does, through paths that sh
# or cmd would read as commands, quoted or not.
link_code = """
import os
import O4_GUI_Utils as GUI
for name in ("Custom Scenery $(touch pwned) & echo", "Custom_Scenery&%OS%"):
    base = os.path.abspath(name)
    target = os.path.join(base, "zOrtho4XP_+45+005_build")
    link = os.path.join(base, "zOrtho4XP_+45+005")
    os.makedirs(target)
    GUI.link_dir(target, link)
    if not os.path.samefile(link, target):
        sys.exit(link + ": links elsewhere than " + target)
if os.path.exists("pwned"):
    sys.exit("linking ran a command from the path")
"""

# A Global Scenery DSF in the text form of DSFTool, with the two rasters
# that Step 3 copies, a beach that overlays leave out by default, a
# forest, and a road. It goes in a 7z archive, as in X-Plane 12.
global_scenery_code = """
import os
import subprocess
import numpy
import O4_Overlay_Utils as OVL
gs = sys.argv[1]
dsf_dir = os.path.join(gs, "Earth nav data", "+40+000")
os.makedirs(dsf_dir)
lines = [
    "I", "800", "DSF2TEXT",
    "PROPERTY sim/west 5", "PROPERTY sim/east 6",
    "PROPERTY sim/north 46", "PROPERTY sim/south 45",
    "PROPERTY sim/planet earth",
    "POLYGON_DEF lib/g10/beaches/test.bch",
    "POLYGON_DEF lib/g10/forests/test.for",
    "NETWORK_DEF lib/g10/roads.net",
]
n = numpy.arange(101 * 101, dtype = "<i2")
elevation = n % 300
# The bathymetry of every other cell lies above the elevation, which
# Step 3 lowers to 2 m below it.
bathymetry = elevation - 100 + 150 * (n % 2)
for (i, (name, alt)) in enumerate((("elevation", elevation), ("bathymetry", bathymetry))):
    raw = os.path.join(gs, name + ".raw")
    alt.astype("<i2").tofile(raw)
    lines.insert(i + 11, "RASTER_DEF " + name)
    lines.append("RASTER_DATA version=1 bpp=2 flags=5 width=101 height=101 scale=1.0 offset=0.0 " + raw)
for (kind, lon) in ((0, 5.3), (1, 5.5)):
    lines += ["BEGIN_POLYGON " + str(kind) + " 255 2", "BEGIN_WINDING"]
    for (x, y) in ((0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)):
        lines.append("POLYGON_POINT " + str(lon + x) + " " + str(45.5 + y))
    lines += ["END_WINDING", "END_POLYGON"]
lines += ["BEGIN_SEGMENT 0 1 1 5.1 45.1 0", "END_SEGMENT 2 5.2 45.2 0"]
text = os.path.join(gs, "+45+005.txt")
with open(text, "w") as f:
    f.write("\\n".join(lines) + "\\n")
dsf = os.path.join(gs, "+45+005.dsf")
subprocess.run([OVL.dsftool_cmd.strip(), "-text2dsf", text, dsf], stdout = subprocess.DEVNULL, check = True)
subprocess.run([OVL.seven_zip(), "a", os.path.join(gs, "+45+005.7z"), dsf], stdout = subprocess.DEVNULL, check = True)
os.replace(os.path.join(gs, "+45+005.7z"), os.path.join(dsf_dir, "+45+005.dsf"))
"""

# Step 3 with an overlay source DSF made of properties only.
no_rasters_code = """
import os
import subprocess
import O4_DSF_Utils as DSF
import O4_Overlay_Utils as OVL
OVL.custom_overlay_src = sys.argv[1]
dsf_dir = os.path.join(OVL.custom_overlay_src, "Earth nav data", "+40+000")
os.makedirs(dsf_dir)
text = os.path.join(OVL.custom_overlay_src, "+45+005.txt")
with open(text, "w") as f:
    f.write("PROPERTY sim/west 5\\nPROPERTY sim/east 6\\nPROPERTY sim/north 46\\nPROPERTY sim/south 45\\n")
subprocess.run([OVL.dsftool_cmd.strip(), "-text2dsf", text, os.path.join(dsf_dir, "+45+005.dsf")], stdout = subprocess.DEVNULL, check = True)
rasters = DSF.extract_elevation_and_bathymetry_data(45, 5)
if rasters != (b"", b""):
    sys.exit("rasters " + repr(rasters)[:60] + ", want none")
"""

# Step 4: extract the overlays of the Global Scenery DSF.
overlay_code = """
import os
import subprocess
import O4_File_Names as FNAMES
import O4_Overlay_Utils as OVL
OVL.custom_overlay_src = sys.argv[1]
if not OVL.build_overlay(45, 5):
    sys.exit("build_overlay failed")
dsf = os.path.join(FNAMES.Overlay_dir, "Earth nav data", "+40+000", "+45+005.dsf")
subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "overlay.txt"], stdout = subprocess.DEVNULL, check = True)
with open("overlay.txt") as f:
    text = f.read()
if "BEGIN_POLYGON 1 " not in text or "BEGIN_POLYGON 0 " in text or "BEGIN_SEGMENT" not in text:
    sys.exit(dsf + ": want the forest and the road, without the beach")
"""

# The sort of a mesh in a folder whose name moulinette prints in another
# encoding than UTF-8: the ANSI code page on Windows, and bytes that are
# not UTF-8 on Linux.
sort_name_code = """
import os
import shutil
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mesh_Utils as MESH
tile = CFG.Tile(45, 5, "")
name = os.fsdecode(b"Tiles \\xe9") if sys.platform == "linux" else "Tiles \u00e9"
os.makedirs(name)
shutil.copy(FNAMES.mesh_file(tile.build_dir, 45, 5), FNAMES.mesh_file(name, 45, 5))
tile.build_dir = name
if not MESH.sort_mesh(tile):
    sys.exit("sort_mesh failed in " + repr(name))
"""

# Shift-click on Build Mesh: moulinette reorders the mesh, which keeps its
# triangles, groups them per texture of the zoomlevel, and numbers the
# vertices in their order of first use. Step 3 then builds the DSF again.
sort_mesh_code = """
import math
import os
import subprocess
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mesh_Utils as MESH
import O4_Overlay_Utils as OVL
import O4_Tile_Utils as TILE

# The vertices, each with its normal, which the sort keeps together, and
# the triangles.
def read(path):
    with open(path) as f:
        lines = f.read().split("\\n")
    i = lines.index("Vertices")
    vertices = [tuple(line.split()) for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    i = lines.index("Normals")
    normals = [tuple(line.split()) for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    vertices = [v + n for (v, n) in zip(vertices, normals, strict = True)]
    i = lines.index("Triangles")
    triangles = [[int(x) for x in line.split()] for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    return (vertices, triangles)

# Each triangle as its corners and type, from its lowest corner on.
def shapes(vertices, triangles):
    out = []
    for t in triangles:
        corners = [vertices[v - 1] for v in t[:3]]
        k = corners.index(min(corners))
        out.append((corners[k:] + corners[:k], t[3]))
    return sorted(out)

# The texture under the centroid of t, as moulinette computes it.
def texture(vertices, t, zl):
    (x, y) = ([float(vertices[v - 1][i]) for v in t[:3]] for i in (0, 1))
    lat = (y[0] + y[1] + y[2]) / 3.0
    scale = 2.0 ** (zl - 5)
    return (int(((x[0] + x[1] + x[2]) / 3.0 / 180.0 + 1.0) * scale),
        int(scale * (1.0 - math.log(math.tan((90.0 + lat) * (math.pi / 360))) / math.pi)))

tile = CFG.Tile(45, 5, "")
tile.read_from_config()
mesh = FNAMES.mesh_file(tile.build_dir, tile.lat, tile.lon)
(vertices, triangles) = read(mesh)
if not MESH.sort_mesh(tile):
    sys.exit("sort_mesh failed")
(sorted_vertices, sorted_triangles) = read(mesh)
errors = []
if shapes(sorted_vertices, sorted_triangles) != shapes(vertices, triangles):
    errors.append("the triangles changed")
order = []
for t in sorted_triangles:
    for v in t[:3]:
        if v > len(order):
            order.append(v)
if order != list(range(1, len(sorted_vertices) + 1)):
    errors.append("the vertices are out of their order of first use")
runs = [texture(sorted_vertices, t, tile.default_zl) for t in sorted_triangles]
runs = [r for (i, r) in enumerate(runs) if i == 0 or r != runs[i - 1]]
if len(runs) != len(set(runs)):
    errors.append("a texture's triangles are apart")
if not TILE.build_tile(tile):
    errors.append("Step 3 failed on the sorted mesh")
dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
if subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "sorted.txt"], stdout = subprocess.DEVNULL).returncode:
    errors.append(dsf + ": DSFTool cannot read it")
if errors:
    sys.exit("\\n".join(errors))
"""

# A mesh of two triangles, whose header moulinette copies as it is.
small_mesh = """MeshVersionFormatted 2 %d %%
Dimension 3

Vertices
4
5.1 45.1 0.001 0
5.2 45.1 0.002 0
5.1 45.2 0.003 0
5.2 45.2 0.004 0

Normals
4
0.1 0.2 0
0.1 0.2 0
0.3 0.4 0
0.3 0.4 0

Triangles
2
1 2 3 1
2 4 3 1
"""

# A mesh with a vertex far outside the earth, whose triangle moulinette
# keeps out of the clusters.
far_mesh = """MeshVersionFormatted 2
Dimension 3

Vertices
5
5.1 45.1 0.001 0
5.2 45.1 0.002 0
5.1 45.2 0.003 0
5.2 45.2 0.004 0
1e30 45.1 0 0

Triangles
3
1 2 3 1
2 4 3 1
5 4 2 1
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
gui.config_window.v_["mesh_zl"].set("17")
gui.config_window.v_["use_masks_for_inland"].set("True")
gui.config_window.apply_changes()
if TILE.skip_downloads is not False or CFG.masks_width != 100:
    errors.append("config window: kept invalid values")
if CFG.get_var("mesh_zl") != 17 or CFG.get_var("use_masks_for_inland") is not True:
    errors.append("config window: dropped valid values")
# Dragging the tile map with the right button moves it. Aqua Tk calls
# the right button 2 before Tk 8.7, and 3 from then on.
gui.open_earth_window()
canvas = gui.earth_window.canvas
import tkinter
aqua = canvas.tk.call("tk", "windowingsystem") == "aqua"
b = 2 if aqua and tkinter.TkVersion < 8.7 else 3
gui.update()
view = (canvas.xview(), canvas.yview())
canvas.event_generate("<ButtonPress-%d>" % b, x = 200, y = 200)
canvas.event_generate("<B%d-Motion>" % b, x = 100, y = 120)
gui.update()
if (canvas.xview(), canvas.yview()) == view:
    errors.append("tile map: a drag with button " + str(b) + " left the map in place")
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

# The name of the texture at zoomlevel zl that covers (lat, lon).
def texture(lat, lon, zl):
    n = 2 ** zl
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return str(y // 16 * 16) + "_" + str(x // 16 * 16) + "_TEST" + str(zl) + ".dds"

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
    with (bz2.open if path.endswith(".bz2") else open)(path, "wt", encoding = "utf-8") as f:
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

def test_tile(work, top, errors):
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
    patch_dir = os.path.join(top, "Patches", "+40+000", tile)
    os.makedirs(patch_dir)
    for (name, ways) in patches.items():
        write_osm(os.path.join(patch_dir, name + ".patch.osm"), ways)
    # A patch of the airport cut short, as a failed download leaves it.
    with open(os.path.join(patch_dir, "LFZZ.patch.osm"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n  <node id="1" lat="45.75" lon="5.25"/>\n')
    os.makedirs(os.path.join(top, "Providers", "Test"))
    for (code, headers) in (("TEST", "fake_headers={'User-Agent': user_agent_ortho4xp}\n"), ("TEST2", "")):
        with open(os.path.join(top, "Providers", "Test", code + ".lay"), "w") as f:
            f.write("grid_type=webmercator\n")
            f.write("url_template=http://127.0.0.1:" + str(server.server_port) + "/{zoom}/{x}/{y}.jpg\n")
            f.write("in_GUI=False\n")
            f.write(headers)
    with open(os.path.join(top, "Providers", "Test", "EVIL.lay"), "w") as f:
        f.write("url_template=http://127.0.0.1:9/{zoom}/{x}/{y}.jpg\n")
        f.write("in_GUI=" + payload + "\n")
        f.write("fake_headers={'User-Agent': " + payload + "}\n")
    build = os.path.join(top, "Tiles", "zOrtho4XP_" + tile)
    os.makedirs(build)
    with open(os.path.join(build, "Ortho4XP_" + tile + ".cfg"), "w") as f:
        for (k, v) in config.items():
            f.write(k + "=" + str(v) + "\n")
        f.write("masks_width=" + payload + "\n")
    # The airports as 1.40 pickled them, which Step 1 replaces.
    legacy_apt = os.path.join(build, "Data" + tile + ".apt")
    open(legacy_apt, "wb").close()
    gs = os.path.join(work, "Global Scenery")
    with open(os.path.join(top, "Ortho4XP.cfg"), "w", encoding = "cp1252") as f:
        f.write("skip_downloads=" + payload + "\n")
        f.write("ovl_exclude_pol=" + payload_list + "\n")
        f.write("custom_overlay_src=" + gs + "\n")
        f.write("custom_scenery_dir=" + scenery_dir + "\n")
    errors += check("globalscenery", global_scenery_code, [gs], work, top, os.path.join(work, "Ortho4XP.log"))
    if errors:
        return errors

    log = os.path.join(work, "Ortho4XP.log")
    german = os.path.join(work, "german.py")
    with open(german, "w") as f:
        f.write(german_code)
    status = run([sys.executable, german, "Ortho4XP.py", str(lat), str(lon)], top, log, 1800)
    server.shutdown()
    if status != 0:
        errors.append("Ortho4XP.py " + str(lat) + " " + str(lon) + ": exit status " + str(status) + ", see " + log)
        return errors

    with open(log, encoding = "utf-8", errors = "replace") as f:
        out = f.read()
    for (cfg, line) in (("Ortho4XP.cfg", "skip_downloads="), ("Ortho4XP.cfg", "ovl_exclude_pol="), ("the tile config", "masks_width=")):
        if "invalid line: " + line not in out:
            errors.append(cfg + ": no warning for " + line + ", which holds code")
    if "fake headers for provider EVIL not valid" not in out:
        errors.append("EVIL.lay: no warning for headers that hold code")
    if "Error in treating LFZZ.patch.osm" not in out or "Auto-patched 1 runway" not in out:
        errors.append("LFZZ.patch.osm: a truncated patch took the place of the runway of the airport")
    if "non closed way" not in out:
        errors.append("open.patch.osm: no warning for an open way")
    if "cell_size must be positive" not in out:
        errors.append("zero.patch.osm: no warning for a cell_size of 0")
    # The mesh reaches the 1200 m top of the hill only when Triangle4XP
    # reads the size of the DEM right. Its altitudes are in units of 100 km.
    mesh = os.path.join(build, "Data" + tile + ".mesh")
    with open(mesh) as f:
        lines = f.read().split("\n")
    i = lines.index("Vertices")
    vertices = [[float(x) for x in line.split()[:3]] for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    top_alt = 100000 * max(v[2] for v in vertices)
    if not 1150 < top_alt < 1201:
        errors.append(mesh + ": highest vertex at " + str(round(top_alt)) + " m, want the 1200 m of the hill")
    # The vertices of the ramp follow its plane profile.
    ((lat0, lon0), (lat1, lon1)) = (ramp[0], ramp[2])
    on_ramp = [(lon, 100000 * z) for (lon, lat, z) in vertices if lon0 <= lon <= lon1 and lat0 <= lat <= lat1]
    off = [(lon, z) for (lon, z) in on_ramp if abs(z - (450 - 30 * (lon - lon0) / (lon1 - lon0))) > 0.5]
    if len(on_ramp) < 10 or off:
        errors.append("ramp.patch.osm: " + str(len(off)) + " of " + str(len(on_ramp)) + " vertices off the profile, such as " + str(off[:3]))
    legacy = os.path.join(work, "legacy.cfg")
    with open(legacy, "w") as f:
        for zone in config["zone_list"] + [payload]:
            f.write("zone_list.append(" + str(zone) + ")\n")
        f.write("use_masks_for_inland=1\nhmin=0\n")
    errors += check("legacy", legacy_code, [legacy, repr(config["zone_list"]), scenery_dir], work, top, log)
    errors += check("7z", seven_zip_code, [], work, top, log)
    errors += check("link", link_code, [], work, top, log)
    errors += check("apt", apt_code, [], work, top, log)
    errors += check("geotiff", geotiff_code, [os.path.join(top, config["custom_dem"])], work, top, log)
    errors += check("opentopography", opentopography_code, [], work, top, log)
    errors += check("community", community_code, [], work, top, log)
    errors += check("extent", extent_code, [payload], work, top, log)
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
    # The texture Y_X_<provider><zl>.dds holds the 16 x 16 tiles of zl from
    # tile X, Y on, which the server sees as /zl/x/y.jpg.
    want = set()
    for path in textures:
        (y, x, rest) = os.path.basename(path).split("_", 2)
        zl = int(rest[-6:-4])
        want |= {(zl, int(x) + i, int(y) + j) for i in range(16) for j in range(16)}
    got = {tuple(int(n) for n in path[1:-4].split("/")) for path in paths}
    if got != want:
        errors.append("server: " + str(len(got - want)) + " tiles requested out of the textures, " + str(len(want - got)) + " missing, such as " + str(sorted(got ^ want)[:3]))
    if any(not str(agent).startswith("Ortho4XP/") for agent in agents):
        errors.append("server: user agents " + str(sorted(map(str, agents))) + ", want Ortho4XP/")
    for path in formats.get("DXT1", [])[:1]:
        mean = numpy.asarray(Image.open(path).convert("RGB"), dtype = float).mean(axis = (0, 1))
        if numpy.abs(mean - served).max() > 6:
            errors.append(path + ": mean color " + str(mean.round(1)) + ", want " + str(served.round(1)))
    apt = os.path.join(build, "Data" + tile + ".apt.json")
    if not os.path.exists(apt):
        errors.append(apt + ": missing")
    if os.path.exists(legacy_apt):
        errors.append(legacy_apt + ": left after Step 1")
    airport = os.path.join(build, "textures", texture(45.75, 5.25, 15))
    if airport not in textures:
        errors.append(airport + ": missing, Step 3 lost the airport bounds")
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
            out = f.read()
        for line in ("PROPERTY sim/west 5", "RASTER_DEF elevation", "RASTER_DEF bathymetry"):
            if line not in out:
                errors.append(text + ": missing " + line)
        # DSFTool writes the rasters beside the text, as dsf.txt.<name>.raw.
        elevation = numpy.fromfile(os.path.join(gs, "elevation.raw"), "<i2")
        bathymetry = numpy.fromfile(os.path.join(gs, "bathymetry.raw"), "<i2")
        for (name, want) in (("elevation", elevation), ("bathymetry", numpy.minimum(bathymetry, elevation - 2))):
            raw = text + "." + name + ".raw"
            if not os.path.exists(raw) or not numpy.array_equal(numpy.fromfile(raw, "<i2"), want):
                errors.append(raw + ": differs from the " + name + " of the Global Scenery DSF, as Step 3 makes it")
    errors += check("overlay", overlay_code, [gs], work, top, log)
    errors += check("norasters", no_rasters_code, [os.path.join(work, "Global Scenery 11")], work, top, log)

    errors += check("sortname", sort_name_code, [], work, top, log)
    errors += check("sortmesh", sort_mesh_code, [], work, top, log)
    errors += test_moulinette(work, top, log)

    poly = os.path.join(work, "square.poly")
    with open(poly, "w") as f:
        f.write("4 2 0 0\n1 0 0\n2 1 0\n3 1 1\n4 0 1\n4 0\n1 1 2\n2 2 3\n3 3 4\n4 4 1\n0\n0\n")
    status = run([os.path.join(top, "Utils", sys_dir, "triangle" + exe), "-pAYPQ", poly], work, log, 300)
    if status != 0 or not all(os.path.exists(os.path.join(work, "square.1." + ext)) for ext in ("node", "ele")):
        errors.append("triangle -pAYPQ: exit status " + str(status))

    # Ortho4XP names the programs it misses at startup.
    triangle = os.path.join(top, "Utils", sys_dir, "triangle" + exe)
    os.rename(triangle, triangle + ".hidden")
    r = subprocess.run([sys.executable, "Ortho4XP.py", "1", "2", "3"], cwd = top, env = offline_env(), capture_output = True, text = True, timeout = 300)
    os.rename(triangle + ".hidden", triangle)
    if r.returncode != 2 or "Missing " + os.path.join(".", "Utils", sys_dir, "triangle" + exe) not in r.stderr:
        errors.append("Ortho4XP.py without triangle: status " + str(r.returncode) + ", stderr " + repr(r.stderr[-300:]))
    return errors

# moulinette on small meshes.
def test_moulinette(work, top, log):
    moulinette = os.path.join(top, "Utils", sys_dir, "moulinette" + exe)
    mesh = os.path.join(work, "small.mesh")
    with open(mesh, "w") as f:
        f.write(small_mesh)
    status = run([moulinette, "0", mesh], work, log, 60)
    with open(mesh) as f:
        header = f.readline()
    errors = []
    if status != 0 or header != small_mesh.splitlines(True)[0]:
        errors.append("moulinette: header " + repr(header) + ", status " + str(status))
    # Every triangle of the tile's mesh lies in one cluster.
    mesh2 = os.path.join(work, "tile.mesh")
    shutil.copy(os.path.join(top, "Tiles", "zOrtho4XP_" + tile, "Data" + tile + ".mesh"), mesh2)
    r = subprocess.run([moulinette, "14", mesh2], cwd = work, stdout = subprocess.PIPE, text = True, timeout = 300)
    counted = sum(int(line.split()[1]) for line in r.stdout.splitlines() if line.strip().startswith("Reordering") and "cluster" in line)
    with open(mesh2) as f:
        lines = f.read().splitlines()
    triangles = int(lines[lines.index("Triangles") + 1])
    if r.returncode != 0 or counted != triangles:
        errors.append("moulinette: clusters of " + str(counted) + " triangles in all, want " + str(triangles))
    # Failures leave the mesh as it was and exit with status 1.
    missing = os.path.join(work, "missing.mesh")
    status = run([moulinette, "0", missing], work, log, 60)
    if status != 1 or os.path.exists(missing):
        errors.append("moulinette on a missing mesh: status " + str(status) + ", want 1 and no file")
    broken = small_mesh[:small_mesh.index("Triangles")]
    with open(mesh, "w") as f:
        f.write(broken)
    status = run([moulinette, "0", mesh], work, log, 60)
    with open(mesh) as f:
        if status != 1 or f.read() != broken:
            errors.append("moulinette on a mesh without triangles: status " + str(status) + ", want 1 and the mesh as it was")
    bad = [
        ("a vertex list cut short", small_mesh.replace("5.1 45.2 0.003 0\n5.2 45.2 0.004 0\n", "")),
        ("a normal of one number", small_mesh.replace("0.3 0.4 0\n0.3 0.4 0\n", "0.3 0.4 0\n0.3\n")),
        ("a triangle list cut short", small_mesh.replace("Triangles\n2\n", "Triangles\n3\n")),
        ("a triangle of three numbers", small_mesh.replace("2 4 3 1\n", "2 4 3\n")),
        ("a line of 300 bytes", small_mesh.replace("5.2 45.1 0.002 0", "5.2 45.1 0.002 0" + " " * 300)),
        ("a triangle on a missing vertex", small_mesh.replace("2 4 3 1\n", "2 5 3 1\n")),
        ("a vertex count beyond int", small_mesh.replace("Vertices\n4\n", "Vertices\n2147483647\n")),
        ("100 triangles on one edge", small_mesh.replace("Triangles\n2\n1 2 3 1\n2 4 3 1\n", "Triangles\n100\n" + "1 2 3 1\n1 3 2 1\n" * 50)),
    ]
    for (what, text) in bad:
        with open(mesh, "w") as f:
            f.write(text)
        status = run([moulinette, "0", mesh], work, log, 60)
        with open(mesh) as f:
            if status != 1 or f.read() != text:
                errors.append("moulinette on a mesh with " + what + ": status " + str(status) + ", want 1 and the mesh as it was")
    with open(mesh, "w") as f:
        f.write(far_mesh)
    status = run([moulinette, "16", mesh], work, log, 60)
    with open(mesh) as f:
        lines = f.read().splitlines()
    i = lines.index("Triangles")
    corners = [int(x) for line in lines[i + 2:] for x in line.split()[:3]]
    if status != 0 or len(corners) != 9 or not all(1 <= x <= 5 for x in corners):
        errors.append("moulinette on a vertex at longitude 1e30: status " + str(status) + ", triangles " + repr(lines[i + 2:]))
    status = run([moulinette, "31", mesh], work, log, 60)
    if status != 2:
        errors.append("moulinette at zoomlevel 31: status " + str(status) + ", want 2")
    # A write that fails, here past the size that RLIMIT_FSIZE allows.
    if os.name != "nt":
        import resource
        import signal
        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))
        with open(mesh, "w") as f:
            f.write(small_mesh)
        r = subprocess.run([moulinette, "0", mesh], cwd = work, stdout = subprocess.DEVNULL, preexec_fn = limit, timeout = 60)
        with open(mesh) as f:
            if r.returncode != 1 or f.read() != small_mesh:
                errors.append("moulinette with a write that fails: status " + str(r.returncode) + ", want 1 and the mesh as it was")
    status = run([moulinette, "0"], work, log, 60)
    if status != 2:
        errors.append("moulinette without a mesh: status " + str(status) + ", want 2")
    return errors

def test_gui(work, top, errors):
    import tkinter
    from PIL import ImageTk
    try:
        tk = tkinter.Tk()
        photo = ImageTk.PhotoImage(Image.new("RGB", (16, 16), color))
        tk.destroy()
    except Exception as e:
        errors.append("Tk: " + repr(e))
        return errors
    log = os.path.join(work, "Ortho4XP.log")
    with open(log, "wb") as f:
        gui = subprocess.Popen([sys.executable, "Ortho4XP.py"], cwd = top, env = offline_env(), stdout = f, stderr = subprocess.STDOUT)
        time.sleep(15)
        status = gui.poll()
        gui.kill()
        gui.wait()
    if status is not None:
        errors.append("GUI exited with status " + str(status))
    with open(log, encoding = "utf-8", errors = "replace") as f:
        out = f.read()
    for text in ("Traceback", "Exception in Tkinter callback"):
        if text in out:
            errors.append("GUI printed " + text + ", see " + log)
    errors += check("gui", gui_code, [payload], work, top, log)
    errors += test_medit(work, top, log)
    return errors

# medit shows a mesh, and runs until stopped. On Windows the kill also
# loses its buffered output. A SIGTERM while freeglut talks to the X server
# can hang medit, which the timeout catches.
def test_medit(work, top, log):
    mesh = os.path.join(work, "medit.mesh")
    with open(mesh, "w") as f:
        f.write(small_mesh.replace(" %d %%", ""))
    medit = subprocess.Popen([os.path.join(top, "Utils", sys_dir, "medit" + exe), mesh], cwd = work, env = offline_env(),
        stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
    time.sleep(5)
    status = medit.poll()
    medit.terminate()
    try:
        out = medit.communicate(timeout = 30)[0].decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        medit.kill()
        medit.communicate()
        return ["medit: still running 30 s after SIGTERM"]
    with open(log, "a", encoding = "utf-8") as f:
        f.write(out)
    if status is not None:
        return ["medit: exit status " + str(status) + " while showing the mesh, see " + log]
    if os.name != "nt" and "Creating scene" not in out:
        return ["medit: no scene for the mesh, see " + log]
    return []

if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["-gui"]):
        print(usage, file = sys.stderr)
        sys.exit(2)
    work = tempfile.mkdtemp(prefix = "Ortho4XP-test-", dir = os.path.dirname(root_dir))
    top = os.path.join(work, "Ortho4XP")
    shutil.copytree(root_dir, top, ignore = ignore)
    # The test functions add to errors, which an exception keeps.
    errors = []
    try:
        (test_gui if sys.argv[1:] else test_tile)(work, top, errors)
    except Exception:
        errors.append(traceback.format_exc().rstrip())
    if errors:
        for error in errors:
            print("test.py: " + error, file = sys.stderr)
        print("test.py: files kept in " + work, file = sys.stderr)
        sys.exit(1)
    shutil.rmtree(work)
