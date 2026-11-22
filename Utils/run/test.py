#!/usr/bin/env python3

# Test Ortho4XP end to end and offline: build the tile +45+005 from a
# generated DEM, cached OSM data, and imagery served by this script, and
# check what Steps 1 to 3 produce. With -gui, check that the GUI starts
# and keeps running, and drive its windows, instead.

import bz2
import collections
import glob
import hashlib
import http.server
import io
import math
import os
import re
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
# steep flank of its hill, which makes Step 1 level it. The river, 55 m
# wide, meshes into triangles from bank to bank that Step 2 splits. The
# pond lies on land in the ZL 14 zone, where inland water takes masks in
# XP12 water.
config = {
    "default_website": "TEST",
    "default_zl": 12,
    "zone_list": [([x for p in box(45.05, 5.05, 45.2, 5.2) for x in p], 14, "TEST2")],
    "mesh_zl": 16,
    "custom_dem": os.path.join("Elevation_data", "test.hgt"),
    "road_level": 1,
    "cover_airports_with_highres": "True",
    "cover_zl": 15,
    "use_decal_on_terrain": True,
}

osm = {
    "airports": [
        (box(45.74, 5.22, 45.76, 5.28), {"aeroway": "aerodrome", "icao": "LFZZ", "name": "Test"}),
        ([(45.75, 5.23), (45.75, 5.27)], {"aeroway": "runway", "width": "45"}),
    ],
    "big_roads": [(line((45.3, 5.47), (45.7, 5.97)), {"highway": "motorway"})],
    "coastline": [(line((45.3, 4.95), (44.95, 5.3)), {"natural": "coastline"})],
    "water": [
        (box(45.84, 5.6, 45.852, 5.616), {"natural": "water", "name": "Test"}),
        (box(45.5, 5.8, 45.5005, 5.9), {"natural": "water", "name": "River"}),
        (box(45.148, 5.148, 45.152, 5.154), {"natural": "water", "name": "Pond"}),
    ],
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

# A tile config in the form of 1.20, which appends zones one per line and
# starts with the byte order mark of Notepad. It holds a bool edited by
# hand to 1, and settings that Ortho4XP has dropped, which only verbosity
# 2 reports: hmin of 1.30, and distance_masks_too and ratio_bathy of 1.40.
# Two settings of text hold a value off their lists, a mistyped water_tech
# and masking_mode, which keep their defaults with a warning.
# The global config is in cp1252, which Python before 3.15 wrote on
# Windows.
legacy_code = """
import ast
import contextlib
import io
import O4_Config_Utils as CFG
tile = CFG.Tile(45, 5, "")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    tile.read_from_config(sys.argv[1])
for name in ("hmin", "distance_masks_too", "ratio_bathy"):
    if name in out.getvalue():
        sys.exit(name + " reported at verbosity 1: " + out.getvalue())
for (name, text, want) in (("water_tech", "XP13", "XP12"), ("masking_mode", "Sand", "sand")):
    if getattr(tile, name) != want or "invalid line: " + name + "=" + text + " (not one of " not in out.getvalue():
        sys.exit(name + "=" + text + " read as " + repr(getattr(tile, name)) + " with " + repr(out.getvalue()) + ", want " + repr(want) + " and a warning")
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
# error. A Global Scenery DSF whose archive is damaged leaves nothing in
# tmp.
seven_zip_code = """
import os
import random
import subprocess
import O4_File_Names as FNAMES
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
    # A 7-Zip on the PATH goes before Program Files, so the PATH holds
    # only the Windows directories here.
    path = os.environ["PATH"]
    os.environ["PATH"] = os.path.expandvars(r"%SystemRoot%\\System32;%SystemRoot%")
    os.rename(cmd, cmd + ".hidden")
    found = OVL.seven_zip()
    os.rename(cmd + ".hidden", cmd)
    os.environ["PATH"] = path
    if found is None or os.path.normcase(found) != os.path.normcase(installed):
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
with open("+45+005.dsf", "wb") as f:
    f.write(random.Random(1).randbytes(65536))
subprocess.run([cmd, "a", "damaged.7z", "+45+005.dsf"], stdout = subprocess.DEVNULL, check = True)
os.remove("+45+005.dsf")
with open("damaged.7z", "rb") as f:
    data = bytearray(f.read())
data[100] ^= 0xff
with open("damaged.7z", "wb") as f:
    f.write(data)
if OVL.unpacked_copy(os.path.abspath("damaged.7z"), 45, 5) is not None or os.path.exists(os.path.join(FNAMES.Tmp_dir, "+45+005.dsf")):
    sys.exit("unpacked_copy: a damaged archive gave a copy, or left one in tmp")
with open("not7z.txt", "w") as f:
    f.write("Ortho4XP")
OVL.seven_zip = lambda: os.path.abspath("not7z.txt")
if OVL.extract_7z("7z.7z", "tmp"):
    sys.exit("extract_7z: success with a text file for 7-Zip")
"""

# write_geotiff writes alt, rows from north to south, as a GeoTIFF of the
# tile: the corner pixels centered on the tile corners when point is true,
# as in SRTM, and pixels tiling the tile otherwise, as in ALOS. The
# tiepoint lies at pixel tie.
geotiff_writer = """
import numpy
from PIL import Image, TiffImagePlugin, TiffTags
(lat, lon) = (45, 5)
def write_geotiff(path, alt, point, nodata, compression, tie = (0, 0)):
    (rows, cols) = alt.shape
    (sx, sy) = (1 / (cols - 1), 1 / (rows - 1)) if point else (1 / cols, 1 / rows)
    (i, j) = tie
    raster_type = 2 if point else 1
    tags = {
        33550: ((sx, sy, 0.0), TiffTags.DOUBLE),
        33922: ((float(i), float(j), 0.0, lon + i * sx, lat + 1 - j * sy, 0.0), TiffTags.DOUBLE),
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
# float32), each with nodata in its northwest corner, and with pixels twice
# as wide as high, tied at an inner pixel. Cells of NaN read as nodata. A
# TIFF without georeference, and one of signed bytes, which Pillow
# misreads, read as zero altitude.
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
    ("srtm.tif", srtm, True, -32768, "tiff_deflate", (0, 0), (0, 0, 1, 1)),
    ("alos.tif", alos, False, -9999, "tiff_lzw", (0, 0), (e, e, 1 - e, 1 - e)),
    ("wide.tif", srtm[:, ::2], True, -32768, "tiff_deflate", (100, 200), (0, 0, 1, 1)),
]
for (path, alt, point, nodata, compression, tie, corners) in cases:
    write_geotiff(path, alt, point, nodata, compression, tie)
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
# with the content type that the extension of the path names, and answers
# 404 for other paths. It returns the URL of the server, and the paths and
# the user agents of the requests, which check_agents requires to name
# Ortho4XP.
file_server = """
import http.server
import mimetypes
import threading
def serve(files):
    (paths, agents) = ([], set())
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            agents.add(self.headers.get("User-Agent"))
            path = self.path.split("?")[0]
            body = files.get(path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            kind = mimetypes.guess_type(path)[0]
            if kind:
                self.send_header("Content-Type", kind)
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
# downloaded tile serves again from Elevation_data, while a cached page or
# empty file, as earlier versions saved, downloads again. An SRTM .hgt
# placed by hand goes before the download, and a missing tile gets a
# warning when it is the tile of the build. The labels of 1.40 name the
# sources too.
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
with open(FNAMES.elevation_data("SRTM", lat, lon), "wb") as f:
    f.write(served["/SRTM_GL1/SRTM_GL1_srtm/N46E005.tif"])
open(FNAMES.elevation_data("ALOS", lat, lon), "wb").close()
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

# A Viewfinderpanoramas tile in Elevation_data serves again at its full
# size only, and one cut short, as 1.40 could leave it, downloads again.
# Viewfinderpanoramas has 3" tiles at 40 5.
viewfinder_code = """
import contextlib
import io
import os
import O4_DEM_Utils as DEM
import O4_File_Names as FNAMES
hgt = FNAMES.viewfinderpanorama(40, 5)
os.makedirs(os.path.dirname(hgt), exist_ok = True)
urls = []
DEM.http_request = lambda url, source, verbose = False: urls.append(url) or 0
for (size, reused) in ((2 * 1201 ** 2, True), (2 * 1201 ** 2 - 1000, False)):
    with open(hgt, "wb") as f:
        f.write(bytes(size))
    urls.clear()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = DEM.ensure_elevation("View", 40, 5)
    if reused and (got != 1 or urls):
        sys.exit(hgt + " of " + str(size) + " bytes: downloaded again, want it reused")
    if not reused and (not urls or "is damaged" not in out.getvalue()):
        sys.exit(hgt + " of " + str(size) + " bytes: reused, want a warning and a download")
os.remove(hgt)
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

# X-Plane 12 loads the Demo Areas before the Global Scenery, and the
# Demo Areas alone hold some tiles: the DSF of a tile comes from the Demo
# Areas beside custom_overlay_src when they have it.
demo_code = """
import os
import O4_Overlay_Utils as OVL
root = os.path.abspath("demo")
gs = os.path.join(root, "X-Plane 12 Global Scenery")
demo = os.path.join(root, "X-Plane 12 Demo Areas")
for d in (gs, demo):
    os.makedirs(os.path.join(d, "Earth nav data", "+40+000"))
    open(os.path.join(d, "Earth nav data", "+40+000", "+45+005.dsf"), "w").close()
cases = (
    (gs + os.sep, 45, 5, os.path.join(demo, "Earth nav data", "+40+000", "+45+005.dsf")),
    (gs, 46, 5, os.path.join(gs, "Earth nav data", "+40+000", "+46+005.dsf")),
    ("", 45, 5, os.path.join("Earth nav data", "+40+000", "+45+005.dsf")),
)
for (src, lat, lon, want) in cases:
    OVL.custom_overlay_src = src
    got = OVL.global_scenery_dsf(lat, lon)
    if got != want:
        sys.exit("global_scenery_dsf(" + str(lat) + ", " + str(lon) + ") with custom_overlay_src " + repr(src) + ": " + repr(got) + ", want " + repr(want))
"""

# Cleaning level 2 removes the textures that no terrain file uses, and
# keeps those of land, masked water, and overlay terrain alike.
unwanted_code = """
import os
import O4_Config_Utils as CFG
import O4_Tile_Utils as TILE
tile = CFG.Tile(45, 5, "")
tile.build_dir = "unwanted"
for name in ("terrain", "textures"):
    os.makedirs(os.path.join(tile.build_dir, name))
for ter in ("1_2_A16_sea.ter", "1_2_B16_sea_overlay.ter", "1_2_C16_water_overlay.ter", "1_2_D16.ter"):
    open(os.path.join(tile.build_dir, "terrain", ter), "w").close()
for dds in ("1_2_A16.dds", "1_2_B16.dds", "1_2_C16.dds", "1_2_D16.dds", "1_2_E16.dds"):
    open(os.path.join(tile.build_dir, "textures", dds), "w").close()
TILE.remove_unwanted_textures(tile)
left = sorted(os.listdir(os.path.join(tile.build_dir, "textures")))
if left != ["1_2_A16.dds", "1_2_B16.dds", "1_2_C16.dds", "1_2_D16.dds"]:
    sys.exit("textures left " + str(left) + ", want all but 1_2_E16.dds")
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

# A Global Scenery DSF in the text form of DSFTool, with rasters named as
# X-Plane 12's, a beach that overlays leave out by default, a forest, and a
# road. Step 3 copies its soundscape and its season, and computes the
# elevation and sea_level. The sea_level is of 256 by 256 cells, as some
# older Global Scenery DSFs hold. It goes in a 7z archive, as in X-Plane 12.
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
rasters = (
    ("elevation", 2, 5, 101, 1.0, "<i2"),
    ("sea_level", 2, 1, 256, 1.0, "<i2"),
    ("soundscape", 1, 6, 12, 1.0, "u1"),
    ("spr1", 1, 6, 8, 1.99454, "u1"),
)
for (i, (name, bpp, flags, size, scale, kind)) in enumerate(rasters):
    raw = os.path.join(gs, name + ".raw")
    (numpy.arange(size * size) % (250 if kind == "u1" else 300)).astype(kind).tofile(raw)
    lines.insert(i + 11, "RASTER_DEF " + name)
    lines.append("RASTER_DATA version=1 bpp=" + str(bpp) + " flags=" + str(flags) + " width=" + str(size) + " height=" + str(size) + " scale=" + str(scale) + " offset=0.0 " + raw)
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

# Triangle4XP runs at each minimum angle in turn until one succeeds.
# run_triangle4xp returns the angle that worked, or None when every run
# fails.
triangle4xp_code = """
import io
import O4_Mesh_Utils as MESH
runs = []
class Popen:
    def __init__(self, cmd, **kwargs):
        runs.append(cmd[1])
        self.stdout = io.BytesIO(b"Triangle4XP\\n")
    def wait(self):
        return 0 if runs[-1] in good else 1
MESH.subprocess.Popen = Popen
tests = [
    ([10, 0], {"-pq10uY"}, 10, ["-pq10uY"]),
    ([10, 0], {"-pq0uY"}, 0, ["-pq10uY", "-pq0uY"]),
    ([10, 0], set(), None, ["-pq10uY", "-pq0uY"]),
    ([10], {"-pq0uY"}, None, ["-pq10uY"]),
]
for (angles, good, want, want_runs) in tests:
    runs.clear()
    got = MESH.run_triangle4xp(["Triangle4XP", "", "x.poly"], angles, "uY")
    if got != want or runs != want_runs:
        sys.exit("run_triangle4xp at " + str(angles) + " with " + str(sorted(good)) + " succeeding: " + str(got) + " after " + str(runs) + ", want " + str(want) + " after " + str(want_runs))
"""

# A mesh of Triangle4XP: a river from y = 0 to y = 1 between banks of
# land, of two water triangles whose vertices all lie on the shore, and
# whose common edge bridges the river from the south bank to the north
# one. East of it, the water reaches nodes 7 and 8 off the shore, so the
# edge from (1, 0) to (1, 1) is a bridge too, and the triangles beyond
# are not beached. The vector altitudes of the vertices are 10, 20, 30,
# 40, 0, 0, 50, and 60. river(base, segments) writes it with the given
# segments.
river = """
nodes = [(0, 0, 10), (1, 0, 20), (0, 1, 30), (1, 1, 40), (0.5, -1, 0), (0.5, 2, 0), (1.5, 0.5, 50), (2, 0, 60)]
def river(base, segments):
    with open(base + ".1.node", "w") as f:
        f.write(str(len(nodes)) + " 2 4 0\\n")
        for (k, (x, y, a)) in enumerate(nodes):
            f.write(" ".join(str(v) for v in (k + 1, x, y, 0, 0, 0, a)) + "\\n")
        f.write("# Generated by Triangle4XP\\n")
    with open(base + ".1.ele", "w") as f:
        f.write("6 3 1\\n1 1 2 3 2\\n2 2 4 3 2\\n3 1 5 2 0\\n4 3 4 6 0\\n5 2 7 4 2\\n6 2 8 7 2\\n# Generated by Triangle4XP\\n")
    with open(base + ".1.poly", "w") as f:
        f.write("0 2 4 0\\n" + str(len(segments)) + " 0\\n")
        for (k, (a, b)) in enumerate(segments):
            f.write(str(k + 1) + " " + str(a) + " " + str(b) + "\\n")
        f.write("0\\n# Generated by Triangle4XP\\n")
"""

# The splits of the river: the centroids of its two triangles, and the
# middles of the bridges, but for one that is a segment of the mesh.
splits_code = river + """
import numpy
import O4_Mesh_Utils as MESH
tests = [
    ([], 2, [(1 / 3, 1 / 3, 20), (2 / 3, 2 / 3, 30), (0.5, 0.5, 25), (1, 0.5, 30)]),
    ([(1, 5), (3, 2)], 1, [(1 / 3, 1 / 3, 20), (2 / 3, 2 / 3, 30), (1, 0.5, 30)]),
]
for (segments, want_bridges, want) in tests:
    river("river", segments)
    (points, beached, bridges) = MESH.water_splits("river.1.node", "river.1.ele", "river.1.poly")
    got = sorted(tuple(p) for p in points)
    want = sorted(want)
    if (beached, bridges) != (2, want_bridges) or len(got) != len(want) or numpy.abs(numpy.array(got) - numpy.array(want)).max() > 1e-9:
        sys.exit("water_splits with segments " + str(segments) + ": " + str(got) + " from " + str(beached) + " beached triangles and " + str(bridges) + " bridges, want " + str(want) + " from 2 and " + str(want_bridges))
"""

# split_water meshes the tile again with the splits of the river added to
# the nodes of Step 1, and leaves that mesh in the place of the first
# run's. When the run fails, the first run's mesh stays, with a warning.
# The run's files go either way, those of an earlier run before it, and
# the first run's .poly with cleaning, after a stop too, which skips the
# run.
split_code = river + """
import contextlib
import io
import os
import shutil
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mesh_Utils as MESH
import O4_UI_Utils as UI
tile = CFG.Tile(10, 10, "")
os.makedirs(tile.build_dir)
base = FNAMES.input_poly_file(tile)[: -len(".poly")]
with open(base + ".node", "w") as f:
    f.write("8 2 1 0\\n" + "".join(str(k + 1) + " " + str(x) + " " + str(y) + " " + str(a) + "\\n" for (k, (x, y, a)) in enumerate(nodes)))
with open(base + ".poly", "w") as f:
    f.write("0 2 1 0\\n\\n1 1\\n1 3 2 0\\n\\n0\\n\\n0\\n")
runs = []
class Popen:
    def __init__(self, cmd, **kwargs):
        with open(cmd[-1][: -len(".poly")] + ".node") as f:
            runs.append((cmd[1], f.read().split("\\n")))
        for ext in (".node", ".ele"):
            with open(cmd[-1][: -len(".poly")] + ".1" + ext, "w") as f:
                f.write("second")
        self.stdout = io.BytesIO(b"")
    def wait(self):
        return status
MESH.subprocess.Popen = Popen
switches = lambda n, poly: "uY" + ("" if poly else "P") + "S" + str(n)
# Its cases: Triangle4XP's status, the cleaning level, a .poly left by an
# earlier run, a stop, the switches of the run, and the mesh that stays.
cases = [
    (1, 1, False, False, "-pq10uYPS11", "first"),
    (0, 0, False, False, "-pq10uYS11", "second"),
    (0, 1, True, False, "-pq10uYPS11", "second"),
    (0, 1, False, True, None, "first"),
]
for (status, cleaning, stale, stop, switch, mesh) in cases:
    river(base, [(3, 2)])
    if stale:
        with open(base + ".split.1.poly", "w") as f:
            f.write("stale")
    with open(base + ".1.node") as f:
        first = f.read()
    UI.cleaning_level = cleaning
    UI.red_flag = stop
    runs.clear()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        MESH.split_water(tile, ["Triangle4XP", "", base + ".poly"], 10, switches)
    UI.red_flag = False
    if [r[0] for r in runs] != [switch][: switch is not None]:
        sys.exit("split_water: runs " + str([r[0] for r in runs]) + ", want " + str(switch))
    for (run, lines) in runs:
        added = [[float(v) for v in line.split()] for line in lines[9:12]]
        if lines[0] != "11 2 1 0" or [a[0] for a in added] != [9, 10, 11] or abs(added[2][1] - 1) > 1e-9 or abs(added[2][2] - 0.5) > 1e-9 or abs(added[2][3] - 30) > 1e-9:
            sys.exit("split_water: nodes " + str(lines[:1] + lines[9:12]) + ", want 11 of them ending in node 11 at (1, 0.5, 30)")
    with open(base + ".1.node") as f:
        got = "first" if f.read() == first else "second"
    left = sorted(n for n in os.listdir(tile.build_dir) if ".split." in n)
    if got != mesh or left or os.path.exists(base + ".1.poly") == bool(cleaning):
        sys.exit("split_water with Triangle4XP returning " + str(status) + ", cleaning " + str(cleaning) + ", a stale .poly " + str(stale) + ", and a stop " + str(stop) + ": the " + got + " mesh, " + str(left) + " left, " + str(os.path.exists(base + ".1.poly")) + " for the .1.poly, want the " + mesh + " mesh")
    if ("WARNING: The second run of Triangle4XP failed" in out.getvalue()) != (status != 0 and not stop):
        sys.exit("split_water with Triangle4XP returning " + str(status) + " printed " + repr(out.getvalue()))
shutil.rmtree(tile.build_dir)
"""

# The recut of Step 3 leaves whole the edge of a water triangle that lies
# on the south edge of the tile between two vertices of the shore, as the
# tile south of it has no vertex in its middle, and splits the triangle at
# its centre instead.
recut_code = """
import numpy
import O4_Bathymetry as BATHY
points = [(5.1, 45), (5.3, 45), (5.2, 45.1), (5, 45), (5.4, 45)]
nodes = numpy.array([v for (x, y) in points for v in (x, y, 0, 0, 0)], float)
tris = numpy.array([0, 1, 2, 3, 0, 2, 1, 4, 2], numpy.uint32)
types = numpy.array([2, 0, 0], numpy.uint32)
(n, coords, coast, m, idx, types) = BATHY.recut_water_tris(nodes, tris, types)
new = [tuple(coords[5 * k : 5 * k + 2]) for k in range(5, n)]
if m != 5 or [p for p in new if p[1] == 45] or not numpy.allclose(new, [(5.2, 45 + 0.1 / 3)]):
    sys.exit("recut: new nodes " + str(new) + " and " + str(m) + " triangles, want the centre of the water triangle and 5")
"""

# The colour of X-Plane's water, from the map of a fake X-Plane 12 whose
# Global Scenery is custom_overlay_src: 4 by 4 pixels over +45+005, row 0
# to the north, red rising to the east and green to the south. A tile
# without a map takes the colour of any.png, and 180 E that of the map of
# +45-180, uniform. Over +44+005, the red of a
# map of 512 by 512 pixels follows a sine of 64 pixels from west to east,
# and the colour image of a texture at ZL 14 there holds the colour at
# the centre of each of its pixels. XP12 textures fade from the photo
# where the mask is white into that colour where it is black, as DXT1,
# while XP11 textures take the mask as their alpha. dds_header reads the
# four-character code of a DDS and the CRC of the colours it fades into,
# and masked_dds_header gives those of the texture of masked water in
# each technique.
water_colour_code = """
import os
import numpy
from PIL import Image
import O4_Config_Utils as CFG
import O4_Geo_Utils as GEO
import O4_Imagery_Utils as IMG
import O4_Overlay_Utils as OVL
root = os.path.abspath("X-Plane 12")
gs = os.path.join(root, "Global Scenery", "X-Plane 12 Global Scenery")
water = os.path.join(root, "Resources", "bitmaps", "world", "water", "+40+000")
os.makedirs(gs)
os.makedirs(water)
rgb = numpy.zeros((4, 4, 3), numpy.uint8)
(j, i) = numpy.mgrid[0:4, 0:4]
rgb[..., 0] = 10 + 20 * i
rgb[..., 1] = 10 + 20 * j
rgb[..., 2] = 50
Image.fromarray(rgb).save(os.path.join(water, "+45+005.png"))
os.makedirs(os.path.join(water, "..", "+40-180"))
Image.new("RGB", (4, 4), (30, 60, 90)).save(os.path.join(water, "..", "+40-180", "+45-180.png"))
OVL.custom_overlay_src = gs + os.sep
errors = []
tests = [
    ((45.875, 5.125), (10, 10, 50)),
    ((45.125, 5.875), (70, 70, 50)),
    ((45.75, 5.5), (40, 20, 50)),
    ((45.99, 5.01), (10, 10, 50)),
    ((46.5, 5.5), OVL.open_sea_colour),
    ((45.5, 180.0), (30, 60, 90)),
]
for ((lat, lon), want) in tests:
    got = OVL.water_colour(lat, lon)
    if numpy.abs(got - want).max() > 1e-6:
        errors.append("water_colour" + str((lat, lon)) + ": " + str(got) + ", want " + str(want))
w = 512
rgb = numpy.zeros((w, w, 3), numpy.uint8)
rgb[..., 0] = (128 + 127 * numpy.sin(2 * numpy.pi * numpy.arange(w) / 64)).round()
rgb[..., 1] = 100
rgb[..., 2] = 50
Image.fromarray(rgb).save(os.path.join(water, "+44+005.png"))
(til_x, til_y) = GEO.wgs84_to_orthogrid(44.6, 5.3, 14)
size = 512
got = numpy.asarray(IMG.water_colour_image(til_x, til_y, 14, size), float)
for k in range(0, size, 7):
    (lat, lon) = GEO.gtile_to_wgs84(til_x + 16 * (k + 0.5) / size, til_y + 16 * (k // 2 + 0.5) / size, 14)
    want = OVL.water_colour(lat, lon)
    if numpy.abs(got[k // 2, k] - want).max() > 3:
        errors.append("water_colour_image of " + str((til_x, til_y)) + ": " + str(got[k // 2, k]) + " at pixel " + str((k, k // 2)) + ", want " + str(want.round(1)))
        break
tile = CFG.Tile(45, 5, "")
tile.water_tech = "XP12"
photo = Image.new("RGB", (64, 64), (200, 150, 100))
mask = Image.new("L", (64, 64), 255)
mask.paste(0, (32, 0, 64, 64))
(til_x, til_y) = (33536, 23280)
out = IMG.apply_water_mask(tile, photo, mask.resize((32, 32)), til_x, til_y, 16, "dds")
(lat, lon) = IMG.GEO.gtile_to_wgs84(til_x + 12, til_y + 8, 16)
far = OVL.water_colour(lat, lon)
a = numpy.asarray(out, int)
if out.mode != "RGB" or tuple(a[32, 4]) != (200, 150, 100) or numpy.abs(a[32, 60] - far).max() > 2:
    errors.append("XP12 fade: " + out.mode + " " + str(tuple(a[32, 4])) + " and " + str(tuple(a[32, 60])) + ", want RGB, the photo, and " + str(far.round()))
tile.water_tech = "XP11 + bathy"
out = IMG.apply_water_mask(tile, photo.copy(), mask, til_x, til_y, 16, "dds")
if out.mode != "RGBA" or out.getpixel((4, 32)) != (200, 150, 100, 255) or out.getpixel((60, 32))[3] != 0:
    errors.append("XP11 mask: " + out.mode + " " + str(out.getpixel((4, 32))) + " and " + str(out.getpixel((60, 32))) + ", want the mask in the alpha")
crc = IMG.fade_crc(til_x, til_y, 14)
for (fourcc, tag, want) in ((b"DXT1", b"", (b"DXT1", None)), (b"DXT5", b"", (b"DXT5", None)), (b"DXT1", IMG.fade_tag + crc, (b"DXT1", crc))):
    header = bytearray(128)
    header[:4] = b"DDS "
    header[32:32 + len(tag)] = tag
    header[84:88] = fourcc
    with open("header.dds", "wb") as f:
        f.write(header)
    if IMG.dds_header("header.dds") != want:
        errors.append("dds_header of a " + fourcc.decode() + " tagged " + repr(tag) + ": " + str(IMG.dds_header("header.dds")) + ", want " + str(want))
for (tech, imprint, want) in (("XP12", True, (b"DXT1", crc)), ("XP11 + bathy", True, (b"DXT5", None)), ("XP11 + bathy", False, (b"DXT1", None))):
    tile.water_tech = tech
    tile.imprint_masks_to_dds = imprint
    if IMG.masked_dds_header(tile, til_x, til_y, 14, "TEST") != want:
        errors.append("masked_dds_header in " + tech + " with imprint_masks_to_dds " + str(imprint) + ": " + str(IMG.masked_dds_header(tile, til_x, til_y, 14, "TEST")) + ", want " + str(want))
if errors:
    sys.exit("\\n".join(errors))
"""

# Two providers texture one place at one zoom level, on the edge of a
# zone, each with its mask: in XP11 + bathy, both imprint it into a DXT5,
# where the first conversion deleted a mask they shared.
masks_code = """
import os
import shutil
from PIL import Image
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
tile = CFG.Tile(11, 11, "")
tile.water_tech = "XP11 + bathy"
tile.imprint_masks_to_dds = True
textures = os.path.join(tile.build_dir, "textures")
os.makedirs(textures)
os.makedirs(FNAMES.Tmp_dir, exist_ok = True)
(til_x, til_y) = GEO.wgs84_to_orthogrid(11.5, 11.5, 16)
mask = Image.new("L", (256, 256), 255)
mask.paste(0, (0, 0, 256, 128))
codes = ("TEST", "TEST2")
dirs = [FNAMES.jpeg_file_dir_from_attributes(11, 11, 16, IMG.providers_dict[code]) for code in codes]
for (code, d) in zip(codes, dirs):
    os.makedirs(d, exist_ok = True)
    Image.new("RGB", (4096, 4096), (200, 150, 100)).save(os.path.join(d, FNAMES.jpeg_file_name_from_attributes(til_x, til_y, 16, code)))
    mask.save(os.path.join(textures, FNAMES.mask_file(til_x, til_y, 16, code)))
got = []
try:
    for code in codes:
        IMG.convert_texture(tile, til_x, til_y, 16, code)
        with open(os.path.join(textures, FNAMES.dds_file_name_from_attributes(til_x, til_y, 16, code)), "rb") as f:
            got.append(f.read(88)[84:])
finally:
    shutil.rmtree(tile.build_dir)
    for d in dirs:
        shutil.rmtree(d, ignore_errors = True)
if got != [b"DXT5", b"DXT5"]:
    sys.exit("the textures of " + str(codes) + " at one place: " + str(got) + ", want both imprinted in a DXT5")
"""

# A raster of 3 by 3 degrees around the tile, at 1/120 of a degree, read
# whole and in the window over the tile, gives the same floor at the posts
# of the tile, from a window of the tile and 2 pixels around, with
# Pillow, and with GDAL when it is there.
window_code = """
import numpy
from PIL import Image, TiffImagePlugin, TiffTags
import O4_Bathymetry as BATHY
import O4_DEM_Utils as DEM
n = 361
(j, i) = numpy.mgrid[0:n, 0:n]
s = 3 / (n - 1)
tags = {
    33550: ((s, s, 0.0), TiffTags.DOUBLE),
    33922: ((0.0, 0.0, 0.0, 4.0, 47.0, 0.0), TiffTags.DOUBLE),
    34735: ((1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 2, 2048, 0, 1, 4326), TiffTags.SHORT),
    42113: ("-32768", TiffTags.ASCII),
}
ifd = TiffImagePlugin.ImageFileDirectory_v2()
for (tag, (value, kind)) in tags.items():
    ifd[tag] = value
    ifd.tagtype[tag] = kind
Image.fromarray((-1000 + 3 * i - 2 * j).astype(numpy.float32)).save("big.tif", tiffinfo = ifd)
for gdal in [False] + [True] * DEM.has_gdal:
    DEM.has_gdal = gdal
    whole = DEM.read_raster_file("big.tif", 45, 5)
    part = DEM.read_raster_file("big.tif", 45, 5, window = True)
    a = BATHY.raster_at_posts(whole[8], *whole[1:5])
    b = BATHY.raster_at_posts(part[8], *part[1:5])
    if part[8].shape != (125, 125) or numpy.isnan(a).any() or numpy.abs(a - b).max() > 1e-6:
        sys.exit("read_raster_file with GDAL " + str(gdal) + ": a window of " + str(part[8].shape) + ", off the whole by " + str(numpy.nanmax(numpy.abs(a - b))) + ", want 125 by 125 and the same floor")
"""

# Step 2.5 at 180 E: +45+179 holds a sea 0.2 degree wide along its east
# edge, beside +45-180, all land, and +45-180 the same sea along its west
# edge, beside +45+179. The mask at the edge fades the sea into the land
# of the tile across it.
antimeridian_code = """
import os
import shutil
import numpy
from PIL import Image
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
import O4_Mask_Utils as MASK
def write_mesh(build_dir, lon, sea):
    # The mesh of the tile at 45, lon, in bands of land and, with sea, of
    # sea from sea to sea + 0.2, cut into rows of 0.05 degree, as the masks
    # take a triangle by its centre.
    xs = sorted({lon, lon + 1} | ({sea, round(sea + 0.2, 6)} if sea is not None else set()))
    ys = [45 + r / 20 for r in range(21)]
    n = len(xs)
    os.makedirs(build_dir, exist_ok = True)
    with open(FNAMES.mesh_file(build_dir, 45, lon), "w") as f:
        f.write("MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n" + str(len(ys) * n) + "\\n")
        for y in ys:
            for x in xs:
                f.write(str(x) + " " + str(y) + " 0 0\\n")
        f.write("\\nNormals\\n" + str(len(ys) * n) + "\\n" + "0 0 0\\n" * (len(ys) * n))
        f.write("\\nTriangles\\n" + str(2 * (n - 1) * (len(ys) - 1)) + "\\n")
        for r in range(len(ys) - 1):
            for k in range(n - 1):
                kind = 2 if sea is not None and abs(xs[k] - sea) < 1e-9 else 0
                (a, b) = (r * n + k + 1, (r + 1) * n + k + 1)
                f.write("%d %d %d %d\\n%d %d %d %d\\n" % (a, a + 1, b + 1, kind, a, b + 1, b, kind))
errors = []
for (lon, beside, sea) in ((179, -180, 179.8), (-180, 179, -180)):
    tile = CFG.Tile(45, lon, "")
    beside_dir = tile.build_dir.replace(FNAMES.tile_dir(45, lon), FNAMES.tile_dir(45, beside))
    write_mesh(tile.build_dir, lon, sea)
    write_mesh(beside_dir, beside, None)
    # The mask of the texture at 180 E, in the middle of the tile.
    (til_x, til_y) = GEO.wgs84_to_orthogrid(45.5, 179.99 if lon == 179 else -179.99, tile.mask_zl)
    name = os.path.join(FNAMES.mask_dir(45, lon), FNAMES.legacy_mask(til_x, til_y))
    try:
        built = MASK.build_masks(tile)
        mask = numpy.asarray(Image.open(name)) if os.path.isfile(name) else None
    finally:
        shutil.rmtree(tile.build_dir)
        shutil.rmtree(beside_dir)
        shutil.rmtree(FNAMES.mask_dir(45, lon), ignore_errors = True)
    if mask is None:
        errors.append("Step 2.5 of " + FNAMES.short_latlon(45, lon) + ": " + str(built) + ", without " + name)
        continue
    edge = mask[:, -4:] if lon == 179 else mask[:, :4]
    if not built or edge.mean() < 64:
        errors.append("Step 2.5 of " + FNAMES.short_latlon(45, lon) + ": " + str(built) + ", the mask at 180 E averaging " + str(round(edge.mean(), 1)) + ", want 1 and the sea fading into the land beside")
if errors:
    sys.exit("\\n".join(errors))
"""

# Step 2.5 and Step 3 on a tile of its own, +44+005, at mask_zl 16, where
# a ZL 16 texture lies on one mask tile. The mesh is a grid on eighths of
# the mask tiles, of land but for four blocks of water, each about a mask
# tile named by its north west corner in ZL 16 tiles:
# - A: a lake over A and 3 eighths around it, more than the margin of its
#   pre-mask, which is all water: its mask is all black;
# - B: the sea over B and 1 eighth around it, with land in the rest of
#   its margin: all black, by the blur;
# - C: the sea over the west half of C: a coast;
# - D: the sea in the second eighth of the tile east of D, which reaches
#   the margin of D alone: all white.
# Step 2.5 saves each mask. In XP12 water without downloads, the middle of
# the lake is X-Plane's water, outside every overlay, and the shore of the
# lake an overlay of the sea. A GeoTIFF export takes the alpha of a coast
# alone, at mask_zl or above. Then a ZL 15 zone covers the texture whose
# north east quarter is A, and whose other quarters hold the shore of the
# lake. Its overlay of the sea takes the four masks, shrunk to 4096 pixels,
# and the middle of the lake stays X-Plane's water. Without the mask of A,
# as an earlier Step 2.5 would leave it, and in a grouped tile, the lake
# keeps the overlay of the photo, with a warning for the missing mask.
complete_masks_code = """
import contextlib
import glob
import io
import os
import shutil
import subprocess
from math import log, pi, tan
from PIL import Image
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
import O4_Mask_Utils as MASK
import O4_Overlay_Utils as OVL
import O4_Tile_Utils as TILE
tile = CFG.Tile(44, 5, "")
(tile.mask_zl, tile.mesh_zl, tile.default_zl, tile.masks_width) = (16, 16, 16, 20)
tile.default_website = "TEST"
(A, B, C, D) = ((33712, 23616), (33792, 23616), (33712, 23712), (33792, 23712))
# The blocks of water in ZL 16 tiles, as (west, east, north, south, type).
water = [
    (A[0] - 6, A[0] + 22, A[1] - 6, A[1] + 22, 1),
    (B[0] - 2, B[0] + 18, B[1] - 2, B[1] + 18, 2),
    (C[0], C[0] + 8, C[1], C[1] + 16, 2),
    (D[0] + 18, D[0] + 20, D[1] + 4, D[1] + 12, 2),
]
# The grid in ZL 16 tiles, from the west and the north edges of the tile.
def tile_x(lon):
    return (lon + 180) / 360 * 65536
def tile_y(lat):
    return (1 - log(tan((90 + lat) * pi / 360)) / pi) / 2 * 65536
xs = [tile_x(5)] + list(range(33680, 33861, 2)) + [tile_x(6)]
ys = [tile_y(45)] + list(range(23576, 23830, 2)) + [tile_y(44)]
lons = [5.0] + [GEO.gtile_to_wgs84(x, 0, 16)[1] for x in xs[1:-1]] + [6.0]
lats = [45.0] + [GEO.gtile_to_wgs84(0, y, 16)[0] for y in ys[1:-1]] + [44.0]
def kind(i, j):
    (x, y) = ((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2)
    return next((k for (w, e, n, s, k) in water if w <= x < e and n <= y < s), 0)
os.makedirs(tile.build_dir)
(nx, ny) = (len(xs), len(ys))
with open(FNAMES.mesh_file(tile.build_dir, 44, 5), "w") as f:
    f.write("MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n" + str(nx * ny) + "\\n")
    for lat in lats:
        for lon in lons:
            f.write(repr(lon) + " " + repr(lat) + " 0 0\\n")
    f.write("\\nNormals\\n" + str(nx * ny) + "\\n" + "0 0 0\\n" * (nx * ny))
    f.write("\\nTriangles\\n" + str(2 * (nx - 1) * (ny - 1)) + "\\n")
    for j in range(ny - 1):
        for i in range(nx - 1):
            (a, b) = (j * nx + i + 1, (j + 1) * nx + i + 1)
            f.write("%d %d %d %d\\n%d %d %d %d\\n" % (a, b, b + 1, kind(i, j), a, b + 1, a + 1, kind(i, j)))
masks = FNAMES.mask_dir(44, 5)
def mask(key):
    name = os.path.join(masks, FNAMES.legacy_mask(*key))
    if not os.path.isfile(name):
        return None
    with Image.open(name) as im:
        return im.getextrema()
errors = []
try:
    if not MASK.build_masks(tile):
        errors.append("Step 2.5 failed")
    # The mask tile east of D holds the sea of D, as a coast.
    for (name, key, want) in (("A, the lake", A, (0, 0)), ("B", B, (0, 0)), ("C", C, (0, 255)), ("D", D, (255, 255)), ("east of D", (D[0] + 16, D[1]), (0, 255))):
        if mask(key) != want:
            errors.append("Step 2.5: the mask of " + name + ": " + str(mask(key)) + ", want the extrema " + str(want))
    # GeoTIFF exports take the alpha of a coast alone.
    for (name, key, zl, coast) in (("A", A, 16, False), ("C", C, 16, True), ("D", D, 16, False), ("C at ZL 15", (C[0] // 2, C[1] // 2), 15, False)):
        if (IMG.geotiff_mask(tile, *key, zl) is not None) != coast:
            errors.append("geotiff_mask of " + name + ": " + str(IMG.geotiff_mask(tile, *key, zl)) + ", want " + ("a mask" if coast else "None"))
    TILE.skip_downloads = True
    dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+44+005.dsf")
    def step3():
        # Step 3, and the names of the terrains of its DSF, with its output.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            built = TILE.build_tile(tile)
        if not built:
            errors.append("Step 3 failed: " + out.getvalue()[-300:])
            return ([], out.getvalue())
        subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "complete.txt"], stdout = subprocess.DEVNULL, check = True)
        with open("complete.txt", encoding = "latin-1") as f:
            defs = [line.split()[1] for line in f if line.startswith("TERRAIN_DEF")]
        for name in glob.glob("complete.txt*"):
            os.remove(name)
        return (defs, out.getvalue())
    def terrains(defs, x, y, zl):
        name = "terrain/" + FNAMES.dds_file_name_from_attributes(x, y, zl, "TEST")[:-4]
        return sorted(d[len(name):] for d in defs if d.startswith(name + "_"))
    (defs, out) = step3()
    if terrains(defs, *A, 16):
        errors.append("Step 3: the middle of the lake, A, in " + str(terrains(defs, *A, 16)) + ", want terrain_Water alone")
    if terrains(defs, A[0] - 16, A[1], 16) != ["_sea_overlay.ter"]:
        errors.append("Step 3: the shore of the lake west of A in " + str(terrains(defs, A[0] - 16, A[1], 16)) + ", want the overlay of the sea")
    # The ZL 15 texture whose north east quarter is A.
    (x15, y15) = (A[0] // 2 - 8, A[1] // 2)
    (north, west) = GEO.gtile_to_wgs84(x15, y15, 15)
    (south, east) = GEO.gtile_to_wgs84(x15 + 16, y15 + 16, 15)
    tile.zone_list = [([south, west, south, east, north, east, north, west, south, west], 15, "TEST")]
    (defs, out) = step3()
    if terrains(defs, x15, y15, 15) != ["_sea_overlay.ter"]:
        errors.append("Step 3 at ZL 15: the lake in " + str(terrains(defs, x15, y15, 15)) + ", want its shore in the overlay of the sea alone")
    else:
        ter = os.path.join(tile.build_dir, "terrain", FNAMES.dds_file_name_from_attributes(x15, y15, 15, "TEST")[:-4] + "_sea_overlay.ter")
        with open(ter) as f:
            lines = f.read().split("\\n")
        size = [line.split()[-1] for line in lines if line.startswith("LOAD_CENTER_BORDER")]
        border = [line.split()[1] for line in lines if line.startswith("BORDER_TEX")]
        with Image.open(os.path.join(tile.build_dir, "terrain", border[0])) as im:
            width = im.size[0]
        if size != ["4096"] or width != 4096:
            errors.append("Step 3 at ZL 15: a BORDER_TEX of " + str(width) + " pixels and LOAD_CENTER_BORDER " + str(size) + ", want 4096 for both")
    a = os.path.join(masks, FNAMES.legacy_mask(*A))
    os.rename(a, a + ".kept")
    (defs, out) = step3()
    os.rename(a + ".kept", a)
    if terrains(defs, x15, y15, 15) != ["_water_overlay.ter"] or "masks of Step 2.5 under water in textures below mask_zl are" not in out:
        errors.append("Step 3 at ZL 15 without the mask of A: " + str(terrains(defs, x15, y15, 15)) + ", want the overlay of the lake and a warning")
    tile.grouped = True
    (defs, out) = step3()
    tile.grouped = False
    if terrains(defs, x15, y15, 15) != ["_water_overlay.ter"]:
        errors.append("Step 3 at ZL 15 in a grouped tile: " + str(terrains(defs, x15, y15, 15)) + ", want the overlay of the lake")
finally:
    shutil.rmtree(tile.build_dir, ignore_errors = True)
    shutil.rmtree(masks, ignore_errors = True)
if errors:
    sys.exit("\\n".join(errors))
"""

# needs_mask below mask_zl, with below, pastes the masks under a texture,
# each shrunk to its place, in rows from the north, and white where one is
# missing. At mask_zl 16, a ZL 15 texture holds 2 by 2 masks: the north
# west one a coast, water to the west, the north east one water alone,
# and the south ones missing. A ZL 14 texture holds those two as the third
# and fourth of its first row of 4. A texture under water alone, or under
# no mask, takes none, and so does any texture below mask_zl without below.
assembly_code = """
import os
import shutil
import numpy
from PIL import Image
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mask_Utils as MASK
tile = CFG.Tile(44, 6, "")
tile.mask_zl = 16
masks = FNAMES.mask_dir(44, 6)
os.makedirs(masks)
coast = numpy.full((4096, 4096), 255, numpy.uint8)
coast[:, :2048] = 0
for (x, y, mask) in ((33888, 23616, coast), (33904, 23616, coast * 0), (33920, 23632, coast * 0)):
    Image.fromarray(mask).save(os.path.join(masks, FNAMES.legacy_mask(x, y)))
errors = []
try:
    for (name, (x, y, zl), points) in (
        ("ZL 15", (16944, 11808, 15), {(500, 1000): 0, (1500, 1000): 255, (3000, 1000): 0, (1000, 3000): 255, (3000, 3000): 255}),
        ("ZL 14", (8464, 5904, 14), {(2300, 500): 0, (2800, 500): 255, (3500, 500): 0, (500, 500): 255, (500, 2300): 255}),
    ):
        mask = MASK.needs_mask(tile, x, y, zl, "TEST", below = True)
        got = {p: mask.getpixel(p) for p in points} if mask else mask
        if got != points:
            errors.append("needs_mask of the " + name + " texture: " + str(got) + ", want " + str(points))
    for (name, (x, y, zl), below) in (
        ("under water alone", (16960, 11808, 15), True),
        ("under no mask", (16992, 11808, 15), True),
        ("without below", (16944, 11808, 15), False),
    ):
        if MASK.needs_mask(tile, x, y, zl, "TEST", below = below) is not False:
            errors.append("needs_mask of a ZL 15 texture " + name + ": a mask, want False")
finally:
    shutil.rmtree(masks)
if errors:
    sys.exit("\\n".join(errors))
"""

# read_mesh_file reads a mesh of two triangles alike with line ends of LF
# and of CRLF, and without the final newline, the altitudes multiplied
# back by 100000, and raises ValueError or IndexError on a mesh cut short
# or with a malformed vertex.
read_mesh_code = """
import os
import numpy
import O4_Mesh_Utils as MESH
mesh = "MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n4\\n5.1 45.1 0.001 0\\n5.2 45.1 0.002 0\\n5.1 45.2 0.003 0\\n5.2 45.2 0.004 0\\n\\nNormals\\n4\\n0.1 0.2 0\\n0.1 0.2 0\\n0.3 0.4 0\\n0.3 0.4 0\\n\\nTriangles\\n2\\n1 2 3 1\\n2 4 3 2\\n"
want = (2.0, 4, [5.1, 45.1, 100, 0.1, 0.2, 5.2, 45.1, 200, 0.1, 0.2, 5.1, 45.2, 300, 0.3, 0.4, 5.2, 45.2, 400, 0.3, 0.4], 2, [0, 1, 2, 1, 3, 2], [1, 2])
def read(text):
    with open("read.mesh", "w", newline = "") as f:
        f.write(text)
    try:
        return MESH.read_mesh_file("read.mesh")
    finally:
        os.remove("read.mesh")
for (name, text) in (("LF", mesh), ("CRLF", mesh.replace("\\n", "\\r\\n")), ("no final newline", mesh[:-1])):
    got = read(text)
    if got[0] != want[0] or got[1] != want[1] or not numpy.allclose(got[2], want[2]) or got[3] != want[3] or got[4].tolist() != want[4] or got[5].tolist() != want[5]:
        sys.exit("read_mesh_file of the mesh with " + name + ": " + repr(got) + ", want " + repr(want))
for (name, text) in (("cut short", mesh[: mesh.index("Triangles") + 12]), ("with a malformed vertex", mesh.replace("5.2 45.1", "5.2 x"))):
    try:
        read(text)
    except (ValueError, IndexError):
        continue
    sys.exit("read_mesh_file of a mesh " + name + ": no error")
"""

# The water points of Step 1, in posts from the south west corner of the
# tile: the sea south west of i + j = 299.9, a lake from 600.25 to 640.25
# east and 600.25 to 630.25 north, an airport over the sea, and the edges
# of the coast, of the lake, of the tile, and of two lines north across
# the water at i = 120 and i = 610.3. The points are the posts of the
# lattice that Laminar's rule gives, and none at the airport or within
# half a post of an edge. A tile all of water, without land, gets the
# lattice of 40 posts.
water_points_code = """
from math import sqrt
import numpy
from shapely import geometry
import O4_Bathymetry as BATHY
import O4_Vector_Map as VMAP
import O4_Vector_Utils as VECT
class DEM:
    def alt_vec(self, way):
        return numpy.full(len(way), 7.0)
class Tile:
    dem = DEM()
def p(i, j):
    return (i / 1200, j / 1200)
sea = geometry.Polygon([p(0, 0), p(299.9, 0), p(0, 299.9)])
lake = geometry.Polygon([p(600.25, 600.25), p(640.25, 600.25), p(640.25, 630.25), p(600.25, 630.25)])
airport = geometry.Polygon([p(239.75, 0), p(252.25, 0), p(252.25, 60.25), p(239.75, 60.25)])
vmap = VECT.Vector_Map()
vmap.water += [sea, lake]
ways = [
    [p(0, 0), p(1200, 0), p(1200, 1200), p(0, 1200), p(0, 0)],
    [p(299.9, 0), p(0, 299.9)],
    list(lake.exterior.coords),
    [p(120, 0), p(120, 1200)],
    [p(610.3, 0), p(610.3, 1200)],
]
for way in ways:
    vmap.insert_way(numpy.array([(x, y, 0) for (x, y) in way]), "DUMMY", check = False)
first = vmap.next_node_id
VMAP.include_water_points(vmap, Tile(), airport)
got = {}
for k in range(first, vmap.next_node_id):
    (x, y) = vmap.nodes_dico[k]
    got[(round(x * 1200), round(y * 1200))] = (x * 1200, y * 1200, vmap.data_nodes[k])
cells = numpy.zeros((1200, 1200), bool)
(cj, ci) = numpy.mgrid[0:1200, 0:1200]
cells[ci + cj <= 298] = True
cells[600:630, 600:640] = True
d = BATHY.distance(cells)
want = set()
for j in range(2, 1199, 2):
    for i in range(2, 1199, 2):
        dist = d[j - 1, i - 1]
        skip = next(k for k in (2, 4, 8, 16, 32, 40) if dist <= k or k == 40)
        if i % skip or j % skip:
            continue
        water = i + j < 299.9 or (600.25 < i < 640.25 and 600.25 < j < 630.25)
        at_airport = 239.75 <= i <= 252.25 and j <= 60.25
        near = ((abs(299.9 - i - j) / sqrt(2) < 0.5 and i < 300 and j < 300) or i == 120 or abs(i - 610.3) < 0.5
            or (600.25 < i < 640.25 and 600.25 < j < 630.25
                and min(i - 600.25, 640.25 - i, j - 600.25, 630.25 - j) < 0.5))
        if water and not at_airport and not near:
            want.add((i, j))
errors = []
for (k, (x, y, z)) in got.items():
    if abs(x - k[0]) > 1e-6 or abs(y - k[1]) > 1e-6 or z != 7:
        errors.append("a water point at " + str((x, y, z)) + " in posts, want one on a post at 7 m")
if set(got) != want:
    errors.append("water points " + str(sorted(set(got) - want)[:5]) + " beyond the lattice, and " + str(sorted(want - set(got))[:5]) + " missing from it")
for k in (2, 4, 8, 16, 32, 40):
    if not [1 for (i, j) in want if next(s for s in (2, 4, 8, 16, 32, 40) if d[j - 1, i - 1] <= s or s == 40) == k]:
        errors.append("the lattice of " + str(k) + " posts holds no point, want the case covered")
# encode_MultiPolygon hands back the polygons it encodes, cut to the
# tile, simplified, and above its area limit: not a pond below it.
vmap = VECT.Vector_Map()
encoded = []
pond = geometry.box(0.5, 0.5, 0.5001, 0.5001)
vmap.encode_MultiPolygon({1: geometry.box(-0.5, 0.2, 0.3, 0.4), 2: pond}, lambda way: numpy.zeros(len(way)), "WATER", area_limit = 1e-6, simplify = 1e-9, encoded = encoded)
if len(encoded) != 1 or abs(encoded[0].area - 0.06) > 1e-9:
    errors.append("encode_MultiPolygon: encoded " + str([p.bounds for p in encoded]) + ", want the box cut at the west edge of the tile, of area 0.06, alone")
vmap = VECT.Vector_Map()
vmap.water.append(geometry.box(0, 0, 1, 1))
vmap.insert_way(numpy.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)]), "DUMMY", check = False)
first = vmap.next_node_id
VMAP.include_water_points(vmap, Tile(), geometry.Polygon())
got = {(round(vmap.nodes_dico[k][0] * 1200), round(vmap.nodes_dico[k][1] * 1200)) for k in range(first, vmap.next_node_id)}
want = {(i, j) for i in range(40, 1200, 40) for j in range(40, 1200, 40)}
if got != want:
    errors.append("a tile all of water: " + str(len(got)) + " water points, want the " + str(len(want)) + " of the lattice of 40 posts")
if errors:
    sys.exit("\\n".join(errors))
"""

# The rasters that Step 3 computes from the mesh: the heights of a plane,
# the water posts around water cells, the distance of Laminar's
# CalcWaterBathymetry against a literal port of its loops, and a floor
# truncated toward zero, as X-Plane's tools write it.
rasters_code = """
import math
import numpy
import O4_Bathymetry as BATHY

def laminar(wet):
    (h, w) = wet.shape
    bath = numpy.where(wet, math.inf, 0.0).tolist()
    for (xs, ys) in ((range(w), range(h)), (range(w - 1, -1, -1), range(h)),
                     (range(w - 1, -1, -1), range(h - 1, -1, -1)), (range(w), range(h - 1, -1, -1))):
        for iy in ys:
            for ix in xs:
                v = bath[iy][ix]
                if v <= 0:
                    continue
                b = min(v if iy == 0 else bath[iy - 1][ix], v if iy == h - 1 else bath[iy + 1][ix])
                a = min(v if ix == 0 else bath[iy][ix - 1], v if ix == w - 1 else bath[iy][ix + 1])
                if abs(a - b) < 1:
                    d = (a + b + math.sqrt(2 - (a - b) ** 2)) * 0.5
                else:
                    d = min(a, b) + 1
                if d < v:
                    bath[iy][ix] = d
    return numpy.array(bath)

errors = []
# Two triangles over the tile, of the plane z = 3 lon + 2 lat - 1 (in
# posts) plus 100.
nodes = numpy.zeros(4 * 5)
for (k, (x, y)) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
    nodes[5 * k : 5 * k + 3] = (5 + x, 45 + y, 100 + (3 * x + 2 * y - 1) * 1200)
tris = numpy.array([0, 1, 2, 0, 2, 3])
(elevation, surface) = BATHY.rasterize(nodes, tris, [0, 2], 45, 5)
(j, i) = numpy.mgrid[0:1201, 0:1201]
if numpy.abs(elevation - (100 + 3 * i + 2 * j - 1200)).max() > 1e-6:
    errors.append("rasterize: the plane is off by " + str(numpy.abs(elevation - (100 + 3 * i + 2 * j - 1200)).max()))
# The second triangle, of water, holds the cells on and above the
# diagonal. The water surface at a post is its height there, at the posts
# one post below the diagonal that of the diagonal, and beyond, none. The
# grid of cells reaches 40 cells beyond the tile.
cells = numpy.zeros((1280, 1280), bool)
BATHY.water_cells(nodes, tris, [0, 2], 45, 5, cells)
want = numpy.zeros((1280, 1280), bool)
want[40:1240, 40:1240] = numpy.tri(1200, dtype = bool)
if (cells != want).any():
    errors.append("water_cells: " + str(int(cells.sum())) + " cells, want the " + str(int(want.sum())) + " on and above the diagonal of the tile")
want = numpy.where(j >= i, 100 + 3 * i + 2 * j - 1200, numpy.where(i - j == 1, 100 + 2.5 * (i + j) - 1200, numpy.inf))
off = numpy.isinf(surface) != numpy.isinf(want)
both = ~numpy.isinf(surface) & ~numpy.isinf(want)
off[both] = numpy.abs(surface[both] - want[both]) > 1e-6
if off.any():
    (b, a) = numpy.argwhere(off)[0]
    errors.append("rasterize: water surface " + str(surface[b, a]) + " at post " + str((a, b)) + ", want " + str(want[b, a]))
# Water at two levels: a lake at 100 m inside the bounding box of a
# narrow river at 50 m, 0.1 degree from it, keeps its own surface.
nodes = numpy.array([
    5.1, 45.1, 50, 0, 0, 5.9, 45.9, 50, 0, 0, 5.9, 45.902, 50, 0, 0,
    5.7, 45.2, 100, 0, 0, 5.8, 45.2, 100, 0, 0, 5.8, 45.3, 100, 0, 0], float)
(elevation, surface) = BATHY.rasterize(nodes, numpy.array([0, 1, 2, 3, 4, 5]), [2, 2], 45, 5)
if abs(surface[300, 900] - 100) > 1e-6 or abs(surface[1080, 1078] - 50) > 1e-6:
    errors.append("rasterize: water surfaces " + str((surface[300, 900], surface[1080, 1078])) + " in the lake and on the river, want 100 and 50")
# Tiles of the water south of 45.5 N, which a land strip of 12 posts
# borders on the east of the first: with the water of each other around
# them, both tiles give the same floor along their common edge.
def rects(boxes):
    nodes = []
    tris = []
    types = []
    for (x0, y0, x1, y1, t) in boxes:
        k = len(nodes) // 5
        for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            nodes += [x, y, 0, 0, 0]
        tris += [k, k + 1, k + 2, k, k + 2, k + 3]
        types += [t, t]
    return (numpy.array(nodes, float), numpy.array(tris), numpy.array(types))
a = rects(((5, 45, 5.99, 45.5, 2), (5.99, 45, 6, 45.5, 0), (5, 45.5, 6, 46, 0)))
b = rects(((6, 45, 7, 45.5, 2), (6, 45.5, 7, 46, 0)))
(around_a, around_b) = (numpy.zeros((1280, 1280), bool), numpy.zeros((1280, 1280), bool))
BATHY.water_cells(*b, 45, 5, around_a)
BATHY.water_cells(*a, 45, 6, around_b)
fa = BATHY.rasters(*a, 45, 5, around_a)[1]
fb = BATHY.rasters(*b, 45, 6, around_b)[1]
if (fa[:, 1200] != fb[:, 0]).any():
    k = numpy.flatnonzero(fa[:, 1200] != fb[:, 0])[0]
    errors.append("rasters: floor " + str(fa[k, 1200]) + " and " + str(fb[k, 0]) + " on the two sides of the edge at row " + str(k))
cells = numpy.zeros((3, 3), bool)
cells[1, 1] = True
if not (BATHY.wet_posts(cells) == numpy.array([[0, 0, 0, 0], [0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]], bool)).all():
    errors.append("wet_posts: " + str(BATHY.wet_posts(cells).astype(int).tolist()))
rng = numpy.random.default_rng(1)
for n in range(100):
    wet = rng.random(rng.integers(2, 20, 2)) < rng.random()
    (got, want) = (BATHY.distance(wet), laminar(wet))
    same = numpy.isinf(got) == numpy.isinf(want)
    if not same.all() or numpy.abs(got[~numpy.isinf(got)] - want[~numpy.isinf(want)]).max(initial = 0) > 1e-9:
        errors.append("distance: differs from CalcWaterBathymetry on " + str(wet.astype(int).tolist()))
        break
if not numpy.isinf(BATHY.distance(numpy.ones((5, 7), bool))).all() or BATHY.distance(numpy.zeros((5, 7), bool)).any():
    errors.append("distance: wrong on all water or all land")
# At -5 m and 1.3 posts from land, the floor lies 2.2 m lower, at -7.2,
# which X-Plane's tools truncate to -7 where the floor function gives -8.
got = BATHY.floor(numpy.array([[-5, -5]], numpy.int16), numpy.array([[1.3, 0]]), numpy.array([[numpy.inf, -2.5]]))
if got.tolist() != [[-7, -6]]:
    errors.append("floor: " + str(got.tolist()) + ", want [[-7, -6]]")
if errors:
    sys.exit("\\n".join(errors))
"""

# The water around the tile: the west neighbour has a mesh, all water, the
# north one a Global Scenery DSF whose soundscape is all water but for the
# southmost 10 posts of its westmost column, the east one a truncated mesh,
# which Step 3 passes over, and the others nothing, which counts as water
# too. The tile east of +45+179, a mesh all of land, is +45-180.
neighbours_code = """
import os
import shutil
import subprocess
import numpy
import O4_Config_Utils as CFG
import O4_DSF_Utils as DSF
import O4_File_Names as FNAMES
import O4_Overlay_Utils as OVL
tile = CFG.Tile(45, 5, "")
mesh_dir = tile.build_dir.replace("+45+005", "+45+004")
os.makedirs(mesh_dir)
with open(FNAMES.mesh_file(mesh_dir, 45, 4), "w") as f:
    f.write("MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n4\\n")
    for (x, y) in ((4, 45), (5, 45), (5, 46), (4, 46)):
        f.write(str(x) + " " + str(y) + " 0 0\\n")
    f.write("\\nNormals\\n4\\n" + "0 0 0\\n" * 4 + "\\nTriangles\\n2\\n1 2 3 1\\n1 3 4 1\\n")
gs = os.path.abspath("neighbours")
dsf_dir = os.path.join(gs, "Earth nav data", "+40+000")
os.makedirs(dsf_dir)
sound = numpy.full((1200, 1200), 30, numpy.uint8)
sound[:10, 0] = 50
sound.tofile(os.path.join(gs, "soundscape.raw"))
with open(os.path.join(gs, "+46+005.txt"), "w") as f:
    f.write("I\\n800\\nDSF2TEXT\\nPROPERTY sim/west 5\\nPROPERTY sim/east 6\\nPROPERTY sim/north 47\\nPROPERTY sim/south 46\\n")
    f.write("RASTER_DEF soundscape\\nRASTER_DATA version=1 bpp=1 flags=6 width=1200 height=1200 scale=1.0 offset=0.0 " + os.path.join(gs, "soundscape.raw") + "\\n")
subprocess.run([OVL.dsftool_cmd.strip(), "-text2dsf", os.path.join(gs, "+46+005.txt"), os.path.join(dsf_dir, "+46+005.dsf")], stdout = subprocess.DEVNULL, check = True)
east_dir = tile.build_dir.replace("+45+005", "+45+006")
os.makedirs(east_dir)
with open(FNAMES.mesh_file(east_dir, 45, 6), "w") as f:
    f.write("MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n4\\n6 45 0 0\\n")
OVL.custom_overlay_src = gs
try:
    cells = DSF.neighbour_water(tile)
finally:
    shutil.rmtree(mesh_dir)
    shutil.rmtree(east_dir)
want = numpy.ones((1280, 1280), bool)
want[1240:1250, 40] = False
if (cells != want).any():
    (j, i) = numpy.argwhere(cells != want)[0]
    sys.exit("neighbour_water: " + str(int(cells.sum())) + " water cells, the first wrong at row " + str(j) + " and column " + str(i))
tile = CFG.Tile(45, 179, "")
mesh_dir = tile.build_dir.replace("+45+179", "+45-180")
os.makedirs(mesh_dir)
with open(FNAMES.mesh_file(mesh_dir, 45, -180), "w") as f:
    f.write("MeshVersionFormatted 2\\nDimension 3\\n\\nVertices\\n4\\n")
    for (x, y) in ((-180, 45), (-179, 45), (-179, 46), (-180, 46)):
        f.write(str(x) + " " + str(y) + " 0 0\\n")
    f.write("\\nNormals\\n4\\n" + "0 0 0\\n" * 4 + "\\nTriangles\\n2\\n1 2 3 0\\n1 3 4 0\\n")
try:
    cells = DSF.neighbour_water(tile)
finally:
    shutil.rmtree(mesh_dir)
want = numpy.ones((1280, 1280), bool)
want[40:1240, 1240:] = False
if (cells != want).any():
    (j, i) = numpy.argwhere(cells != want)[0]
    sys.exit("neighbour_water across 180 E: " + str(int(cells.sum())) + " water cells, the first wrong at row " + str(j) + " and column " + str(i))
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
rasters = DSF.global_scenery_rasters(45, 5)
if rasters != []:
    sys.exit("rasters " + repr(rasters)[:60] + ", want none")
"""

# Step 4 on a source DSF that DSFTool cannot read. DSFTool says so on
# standard error, and exits with status 0.
bad_overlay_code = """
import contextlib
import io
import os
import O4_Overlay_Utils as OVL
OVL.custom_overlay_src = sys.argv[1]
dsf_dir = os.path.join(OVL.custom_overlay_src, "Earth nav data", "+40+000")
os.makedirs(dsf_dir)
with open(os.path.join(dsf_dir, "+45+005.dsf"), "w") as f:
    f.write("not a DSF")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    OVL.build_overlay(45, 5)
if "could not be read" not in out.getvalue():
    sys.exit("build_overlay: the error of DSFTool missing from the output: " + repr(out.getvalue()[-300:]))
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
# DSFTool writes a file for each raster of the source DSF beside its text.
left = [f for f in os.listdir(FNAMES.Tmp_dir) if "_tmp_dsf" in f]
if left:
    sys.exit("build_overlay left " + str(sorted(left)) + " in tmp")
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

# Steps 2.5 and 3 again with water_tech XP11 + bathy, whose DSF goes to
# variant.txt in text.
# A twin of the node on the first corner of a triangle splits it into
# itself and a triangle with two corners on one position, as two nodes
# closer than a step of their pool give. That is done to the sea triangle
# nearest the south west corner of the tile, in terrain_Water under the
# overlay of its ZL 12 texture, and to a triangle of inland water, which
# takes an overlay. Two slivers of land at 45.6 N 5.4 E, on new nodes
# with the altitude of the node nearest, turn counter-clockwise in the
# mesh. A pool at any level from 3 to 8 stores the corners of the first
# on one line, and those of the second turned over. The tile config and
# the mesh go back as they were.
variant_code = """
import os
import shutil
import subprocess
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
import O4_Mask_Utils as MASK
import O4_Overlay_Utils as OVL
import O4_Tile_Utils as TILE
from PIL import Image
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
cfg = os.path.join(tile.build_dir, "Ortho4XP_+45+005.cfg")
shutil.copy(cfg, cfg + ".variant")
mesh = FNAMES.mesh_file(tile.build_dir, 45, 5)
shutil.copy(mesh, mesh + ".variant")
with open(mesh) as f:
    lines = f.read().split("\\n")
def twin(j):
    n = int(lines[4])
    m = int(lines[10 + 2 * n])
    k = 11 + 2 * n + j
    (a, b, c, t) = lines[k].split()[:4]
    d = str(n + 1)
    lines[k] = " ".join((d, b, c, t))
    lines.insert(11 + 2 * n + m, " ".join((a, b, d, t)))
    lines[10 + 2 * n] = str(m + 1)
    lines.insert(8 + 2 * n, lines[7 + n + int(a)])
    lines[7 + n] = d
    lines.insert(5 + n, lines[4 + int(a)])
    lines[4] = d
n = int(lines[4])
tris = [lines[11 + 2 * n + j].split() for j in range(int(lines[10 + 2 * n]))]
def sw(v):
    (x, y) = lines[4 + int(v)].split()[:2]
    return float(x) + float(y)
twin(min((j for j in range(len(tris)) if int(tris[j][3]) & 6), key = lambda j: sum(sw(v) for v in tris[j][:3])))
twin(next(j for j in range(len(tris)) if int(tris[j][3]) & 7 == 1))
def node(x, y, like):
    n = int(lines[4])
    lines.insert(8 + 2 * n, lines[7 + n + like])
    lines[7 + n] = str(n + 1)
    lines.insert(5 + n, " ".join([repr(x), repr(y)] + lines[4 + like].split()[2:]))
    lines[4] = str(n + 1)
    return str(n + 1)
def sliver(corners, like):
    (a, b, c) = [node(x, y, like) for (x, y) in corners]
    n = int(lines[4])
    m = int(lines[10 + 2 * n])
    lines.insert(11 + 2 * n + m, " ".join((a, b, c, "0")))
    lines[10 + 2 * n] = str(m + 1)
u = 2 ** -19
(x0, y0) = (5 + round(0.4004 / u) * u, 45 + round(0.6004 / u) * u)
n = int(lines[4])
like = min(range(1, n + 1), key = lambda i: sum(abs(float(p) - q) for (p, q) in zip(lines[4 + i].split()[:2], (x0, y0))))
sliver([(x0, y0), (x0 + 2 * u, y0 + 2 * u), (x0 + u, y0 + u + 1e-9)], like)
sliver([(x0 + 8 * u, y0), (x0 + 11 * u, y0 + u), (x0 + 9 * u, y0 + u / 3 + 1e-9)], like)
with open(mesh, "w") as f:
    f.write("\\n".join(lines))
tile.water_tech = "XP11 + bathy"
masks = FNAMES.mask_dir(45, 5)
shutil.copytree(masks, masks + ".variant")
try:
    if not MASK.build_masks(tile):
        sys.exit("Step 2.5 failed")
    built = TILE.build_tile(tile)
finally:
    os.replace(cfg + ".variant", cfg)
    os.replace(mesh + ".variant", mesh)
    shutil.rmtree(masks)
    os.replace(masks + ".variant", masks)
if not built:
    sys.exit("Step 3 failed")
dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
if subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "variant.txt"], stdout = subprocess.DEVNULL).returncode:
    sys.exit(dsf + ": DSFTool cannot read it")
"""

# Step 3 in XP12 water again. In a grouped tile, after a texture of land
# alone lost its file beside a black mask that a failed conversion left,
# that texture keeps its photo. Then in a tile alone, after a texture of
# the masked sea lost the tag of its fade, and the texture of land gained
# one, the sea texture fades again, tagged with the CRC of its colours,
# and the land one goes back to its photo, untagged. The tile config, the
# DSF, and the textures go back as they were.
rebuild_code = """
import os
import shutil
import numpy
from PIL import Image
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_DSF_Utils as DSF
import O4_File_Names as FNAMES
import O4_Tile_Utils as TILE
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
cfg = os.path.join(tile.build_dir, "Ortho4XP_+45+005.cfg")
dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
textures = os.path.join(tile.build_dir, "textures")
names = {FNAMES.dds_file_name_from_attributes(*a): a for a in DSF.zone_list_to_ortho_dico(tile).values()}
stems = {t[:-4] for t in os.listdir(os.path.join(tile.build_dir, "terrain")) if t.endswith(".ter")}
water = ("_sea", "_sea_overlay", "_water", "_water_overlay")
plain = sorted(s for s in stems if s + ".dds" in names)
sea = next(s for s in plain if s + "_sea" in stems) + ".dds"
land = next(s for s in plain if not any(s + x in stems for x in water)) + ".dds"
before = set(os.listdir(textures))
shutil.copy(cfg, cfg + ".rebuild")
shutil.copy(dsf, dsf + ".rebuild")
for name in (sea, land):
    shutil.copy(os.path.join(textures, name), name)
errors = []
photo = numpy.asarray(Image.open(land).convert("RGB"), float)
def check_land(how):
    got = numpy.asarray(Image.open(os.path.join(textures, land)).convert("RGB"), float)
    if IMG.dds_header(os.path.join(textures, land)) != (b"DXT1", None) or numpy.abs(got - photo).mean() > 2:
        errors.append(how + ": " + land + ": " + str(IMG.dds_header(os.path.join(textures, land))) + " " + str(got.mean(axis = (0, 1)).round()) + ", want a DXT1 of its photo " + str(photo.mean(axis = (0, 1)).round()))
try:
    tile.water_tech = "XP12"
    os.remove(os.path.join(textures, land))
    Image.new("L", (64, 64), 0).save(os.path.join(textures, FNAMES.mask_file(*names[land])))
    tile.grouped = True
    if not TILE.build_tile(tile):
        errors.append("Step 3 of the grouped tile failed")
    check_land("grouped, beside a mask")
    tile.grouped = False
    with open(os.path.join(textures, sea), "r+b") as f:
        f.seek(32)
        f.write(bytes(8))
    with open(os.path.join(textures, land), "r+b") as f:
        f.seek(32)
        f.write(IMG.fade_tag + IMG.fade_crc(*names[land][:3]))
    if not TILE.build_tile(tile):
        errors.append("Step 3 failed")
    want = (b"DXT1", IMG.fade_crc(*names[sea][:3]))
    if IMG.dds_header(os.path.join(textures, sea)) != want:
        errors.append(sea + ": " + str(IMG.dds_header(os.path.join(textures, sea))) + ", want " + str(want))
    check_land("tagged as faded")
finally:
    os.replace(cfg + ".rebuild", cfg)
    os.replace(dsf + ".rebuild", dsf)
    for name in (sea, land):
        os.replace(name, os.path.join(textures, name))
    for f in set(os.listdir(textures)) - before:
        os.remove(os.path.join(textures, f))
if errors:
    sys.exit("\\n".join(errors))
"""

# The mask of the pond, inland water in the ZL 14 zone, holds it as water
# at its centre in XP12 water, where lakes fade as the sea does.
pond_code = """
import os
from PIL import Image
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
(lat, lon) = (45.15, 5.151)
(til_x, til_y) = GEO.wgs84_to_orthogrid(lat, lon, tile.mask_zl)
(px, py) = GEO.wgs84_to_pix(lat, lon, tile.mask_zl)
(px0, py0) = GEO.wgs84_to_pix(*GEO.gtile_to_wgs84(til_x, til_y, tile.mask_zl), tile.mask_zl)
with Image.open(os.path.join(FNAMES.mask_dir(45, 5), FNAMES.legacy_mask(til_x, til_y))) as im:
    value = im.getpixel((px - px0, py - py0))
if value != 0:
    sys.exit("pond: mask " + str(value) + " at its centre, want 0")
"""

# Step 3 with skip_downloads, in both water techniques: the masked sea is
# an overlay whose BORDER_TEX is a PNG mask beside the textures, which
# come from elsewhere, and Step 3 says so in one line, but for XP11 +
# bathy without imprint_masks_to_dds, whose masks stay in PNG files.
# Without the masks of Step 2.5, the pond keeps the overlay of inland
# water in XP12 water. The tile config, the DSF, the masks, and the
# textures go back as they were.
streamer_code = """
import contextlib
import io
import os
import shutil
import subprocess
from PIL import Image
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_DSF_Utils as DSF
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
import O4_Overlay_Utils as OVL
import O4_Tile_Utils as TILE
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
cfg = os.path.join(tile.build_dir, "Ortho4XP_+45+005.cfg")
dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
textures = os.path.join(tile.build_dir, "textures")
terrain = os.path.join(tile.build_dir, "terrain")
masks = FNAMES.mask_dir(45, 5)
before = set(os.listdir(textures))
shutil.copy(cfg, cfg + ".streamer")
shutil.copy(dsf, dsf + ".streamer")
TILE.skip_downloads = True
errors = []
try:
    for (tech, imprint, lines) in (("XP11 + bathy", True, 1), ("XP11 + bathy", False, 0), ("XP12", False, 1)):
        tile.water_tech = tech
        tile.imprint_masks_to_dds = imprint
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            built = TILE.build_tile(tile)
        if not built:
            errors.append(tech + ": Step 3 failed")
            continue
        n = out.getvalue().count("Downloads are skipped")
        if n != lines:
            errors.append(tech + " with imprint_masks_to_dds " + str(imprint) + ": " + str(n) + " lines on the skipped downloads, want " + str(lines))
        ters = sorted(os.listdir(terrain))
        sea = [t for t in ters if t.endswith("_sea_overlay.ter")]
        if not sea or [t for t in ters if t.endswith("_sea.ter")]:
            errors.append(tech + ": terrain " + str([t for t in ters if "_sea" in t]) + ", want the masked sea in overlays alone")
        for t in sea:
            with open(os.path.join(terrain, t)) as f:
                lines = f.read().split("\\n")
            border = [line.split()[1] for line in lines if line.startswith("BORDER_TEX")]
            size = [line.split()[-1] for line in lines if line.startswith("LOAD_CENTER_BORDER")]
            if len(border) != 1 or not border[0].endswith(".png") or not os.path.isfile(os.path.join(terrain, border[0])):
                errors.append(tech + ": " + t + ": BORDER_TEX " + str(border) + ", want one PNG mask beside the textures")
                break
            with Image.open(os.path.join(terrain, border[0])) as im:
                width = im.size[0]
            if size != [str(width)]:
                errors.append(tech + ": " + t + ": LOAD_CENTER_BORDER " + str(size) + " for a mask of " + str(width) + " pixels")
                break
    tile.water_tech = "XP12"
    os.rename(masks, masks + ".streamer")
    with contextlib.redirect_stdout(io.StringIO()):
        built = TILE.build_tile(tile)
    os.rename(masks + ".streamer", masks)
    texture = DSF.zone_list_to_ortho_dico(tile)[GEO.wgs84_to_orthogrid(45.15, 5.151, tile.mesh_zl)]
    want = "terrain/" + FNAMES.dds_file_name_from_attributes(*texture)[:-4] + "_water_overlay.ter"
    subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "streamer.txt"], stdout = subprocess.DEVNULL, check = True)
    with open("streamer.txt", encoding = "latin-1") as f:
        defs = [line.split()[1] for line in f if line.startswith("TERRAIN_DEF")]
    if not built or want not in defs:
        errors.append("XP12 without masks: " + ("terrain " + str(defs) if built else "Step 3 failed") + ", want " + want + " for the pond")
finally:
    if os.path.isdir(masks + ".streamer"):
        os.rename(masks + ".streamer", masks)
    os.replace(cfg + ".streamer", cfg)
    os.replace(dsf + ".streamer", dsf)
    for f in set(os.listdir(textures)) - before:
        os.remove(os.path.join(textures, f))
if errors:
    sys.exit("\\n".join(errors))
"""

# With use_masks_for_inland and masks_use_DEM_too, the mask of the lake,
# on the plateau of the DEM, stays below white at its centre, where the
# DEM alone tells land. The masks of the tile go back as they were.
lake_mask_code = """
import os
import shutil
import numpy
from PIL import Image
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Geo_Utils as GEO
import O4_Mask_Utils as MASK
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
tile.use_masks_for_inland = True
tile.masks_use_DEM_too = True
masks = FNAMES.mask_dir(45, 5)
shutil.copytree(masks, masks + ".lake")
try:
    if not MASK.build_masks(tile):
        sys.exit("Step 2.5 failed")
    (lat, lon) = (45.846, 5.608)
    (til_x, til_y) = GEO.wgs84_to_orthogrid(lat, lon, tile.mask_zl)
    (px, py) = GEO.wgs84_to_pix(lat, lon, tile.mask_zl)
    (px0, py0) = GEO.wgs84_to_pix(*GEO.gtile_to_wgs84(til_x, til_y, tile.mask_zl), tile.mask_zl)
    name = os.path.join(masks, FNAMES.legacy_mask(til_x, til_y))
    if not os.path.isfile(name):
        sys.exit("Step 2.5 made no mask " + name + " for the lake")
    value = numpy.asarray(Image.open(name))[py - py0, px - px0]
    if value >= 255:
        sys.exit(name + ": " + str(value) + " at the centre of the lake, want below 255")
finally:
    shutil.rmtree(masks)
    os.replace(masks + ".lake", masks)
"""

# Step 3 with a custom bathymetry, a GeoTIFF of the tile without data but
# in three blocks of posts: -35 m offshore, +5 m offshore, above the
# surface, which Step 3 passes over for the floor of its rule, and -100 m
# across the coast, which it clips to 50 m below. Its DSF goes to
# bathymetry.txt in text. A corrupt file stops Step 3, which names it.
# Without downloads, Step 3 leaves the textures, and the tile config, the
# DSF, and the textures go back as they were.
bathymetry_code = geotiff_writer + """
import contextlib
import io
import os
import shutil
import subprocess
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
import O4_Config_Utils as CFG
import O4_Overlay_Utils as OVL
import O4_Tile_Utils as TILE
floor = numpy.full((1201, 1201), -32768, numpy.int16)
# Rows run from north to south, post j from the south.
for (i0, i1, j0, j1, value) in ((20, 60, 20, 60, -35), (20, 60, 70, 110, 5), (140, 160, 130, 150, -100)):
    floor[1200 - j1 : 1201 - j0, i0 : i1 + 1] = value
write_geotiff(os.path.abspath("floor.tif"), floor, True, -32768, "tiff_deflate")
with open("corrupt.tif", "wb") as f:
    f.write(b"II*\\0garbage")
tile = CFG.Tile(45, 5, "")
tile.read_from_config()
cfg = os.path.join(tile.build_dir, "Ortho4XP_+45+005.cfg")
dsf = os.path.join(tile.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
textures = os.path.join(tile.build_dir, "textures")
before = set(os.listdir(textures))
shutil.copy(cfg, cfg + ".bathymetry")
shutil.copy(dsf, dsf + ".bathymetry")
TILE.skip_downloads = True
try:
    tile.custom_bathymetry = os.path.abspath("corrupt.tif")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        built = TILE.build_tile(tile)
    if built or "ERROR: could not read the custom bathymetry " + tile.custom_bathymetry + ":" not in out.getvalue():
        sys.exit("Step 3 with a corrupt custom_bathymetry: went on, or gave no error naming it")
    tile.custom_bathymetry = os.path.abspath("floor.tif")
    if not TILE.build_tile(tile):
        sys.exit("Step 3 with a custom bathymetry failed")
    if subprocess.run([OVL.dsftool_cmd.strip(), "-dsf2text", dsf, "bathymetry.txt"], stdout = subprocess.DEVNULL).returncode:
        sys.exit(dsf + ": DSFTool cannot read it")
finally:
    os.replace(cfg + ".bathymetry", cfg)
    os.replace(dsf + ".bathymetry", dsf)
    for f in set(os.listdir(textures)) - before:
        os.remove(os.path.join(textures, f))
"""

# The floor of the bathymetry rerun: the custom one, clipped, at water
# posts inside the blocks and on their edges, which posts of no data
# border, and that of the main build at a water post of the block above
# the surface, at a land post inside a block, and at every post off the
# blocks.
def check_bathymetry(top, work):
    raw = lambda text, name: numpy.fromfile(text + "." + name + ".raw", "<i2").astype(int).reshape(1201, 1201)
    main = raw(os.path.join(work, "dsf.txt"), "sea_level")
    got = raw(os.path.join(top, "bathymetry.txt"), "sea_level")
    errors = []
    for ((i, j), want) in (((40, 40), -35), ((60, 40), -35), ((40, 20), -35), ((40, 90), main[90, 40]), ((150, 140), -50), ((157, 147), main[147, 157])):
        if got[j, i] != want:
            errors.append("bathymetry: sea_level " + str(got[j, i]) + " at post " + str((i, j)) + ", want " + str(want) + " (the rule gave " + str(main[j, i]) + ")")
    (j, i) = numpy.nonzero(got != main)
    off = [(a, b) for (a, b) in zip(i.tolist(), j.tolist())
        if not any(i0 <= a <= i1 and j0 <= b <= j1 for (i0, i1, j0, j1) in ((20, 60, 20, 60), (20, 60, 70, 110), (140, 160, 130, 150)))]
    if off:
        errors.append("bathymetry: the floor changed off the raster, at posts such as " + str(off[:3]))
    return errors

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

# Build All and the batch build stop at the first step that fails, here
# Step 1, as the command line does. A batch build runs Steps 1 and 2 of
# every tile before the other steps of any.
stop_code = """
import O4_Config_Utils as CFG
import O4_Tile_Utils as TILE
steps = []
TILE.VMAP.build_poly_file = lambda tile: steps.append("Step 1") or 0
TILE.MESH.build_mesh = lambda tile: steps.append("Step 2") or 1
tile = CFG.Tile(45, 5, "")
if TILE.build_all(tile) or steps != ["Step 1"]:
    sys.exit("Build All after a failed Step 1: ran " + str(steps) + ", want Step 1 alone")
steps.clear()
if TILE.build_tile_list(tile, [(45, 5), (46, 5)], True, True, False, False, False, False) or steps != ["Step 1"]:
    sys.exit("batch build after a failed Step 1: ran " + str(steps) + ", want Step 1 alone")
steps.clear()
TILE.VMAP.build_poly_file = lambda tile: steps.append("Step 1 " + str(tile.lat)) or 1
TILE.MESH.build_mesh = lambda tile: steps.append("Step 2 " + str(tile.lat)) or 1
TILE.MASK.build_masks = lambda tile: steps.append("Step 2.5 " + str(tile.lat)) or 1
TILE.build_tile = lambda tile: steps.append("Step 3 " + str(tile.lat)) or 1
TILE.OVL.build_overlay = lambda lat, lon: steps.append("overlays " + str(lat)) or 1
tile.make_dirs = lambda: None
want = ["Step 1 45", "Step 2 45", "Step 1 46", "Step 2 46",
    "Step 2.5 45", "Step 3 45", "overlays 45", "Step 2.5 46", "Step 3 46", "overlays 46"]
if not TILE.build_tile_list(tile, [(45, 5), (46, 5)], True, True, True, True, True, False) or steps != want:
    sys.exit("batch build: ran " + str(steps) + ", want " + str(want))
"""

# Custom coastline and water data whose way runs to a node that they
# lack, in a folder of custom files and in the file that merges them:
# Steps 1 and 2 stop on it, and the folder gives no merged file.
custom_osm_code = """
import bz2
import os
import numpy
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Mesh_Utils as MESH
import O4_Vector_Map as VMAP
import O4_Vector_Utils as VECT
osm = ('<?xml version="1.0" encoding="UTF-8"?>\\n<osm version="0.6">\\n'
    + '  <node id="1" lat="46.5" lon="5.5"/>\\n'
    + '  <way id="1">\\n    <nd ref="1"/>\\n    <nd ref="2"/>\\n  </way>\\n</osm>\\n')
tile = CFG.Tile(46, 5, "")
tile.coast_curv_tol = 2 * tile.curvature_tol
weights = numpy.ones((1001, 1001), dtype = numpy.float32)
steps = [
    ("Step 1 coastline", lambda: VMAP.include_sea(VECT.Vector_Map(), tile), FNAMES.custom_coastline, FNAMES.custom_coastline_dir),
    ("Step 1 water", lambda: VMAP.include_water(VECT.Vector_Map(), tile), FNAMES.custom_water, FNAMES.custom_water_dir),
    ("Step 2 coastline", lambda: MESH.build_curv_tol_weight_map(tile, weights), FNAMES.custom_coastline, FNAMES.custom_coastline_dir),
]
for (what, step, merged, folder) in steps:
    custom = os.path.join(folder(46, 5), "corrupted.osm")
    os.makedirs(folder(46, 5), exist_ok = True)
    with open(custom, "w", encoding = "utf-8") as f:
        f.write(osm)
    if step() != 0 or os.path.exists(merged(46, 5)):
        sys.exit(what + ": went on with a folder of corrupted data, or merged it")
    os.remove(custom)
    with bz2.open(merged(46, 5), "wt", encoding = "utf-8") as f:
        f.write(osm)
    if step() != 0:
        sys.exit(what + ": went on with a corrupted file")
    os.remove(merged(46, 5))
"""

# Every provider file loads, and so does every combined provider, which
# Ortho4XP drops with a printed line when it rejects one. Providers/Test
# holds the providers of these checks.
provider_files_code = """
import contextlib
import io
import os
import O4_File_Names as FNAMES
import O4_Imagery_Utils as IMG
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    IMG.initialize_combined_providers_dict()
errors = []
for d in sorted(os.listdir(FNAMES.Provider_dir)):
    if d == "Test" or not os.path.isdir(os.path.join(FNAMES.Provider_dir, d)):
        continue
    for name in sorted(os.listdir(os.path.join(FNAMES.Provider_dir, d))):
        code = name.split(".")[0]
        if "." in name and name.split(".")[-1] == "lay" and code not in IMG.providers_dict and code not in IMG.providers_without_key:
            errors.append(os.path.join("Providers", d, name) + ": rejected")
errors += [line for line in out.getvalue().splitlines() if "combined provider" in line.lower()]
if errors:
    sys.exit("\\n".join(errors))
"""

# A provider that waits for its api_key: Step 3 names its .lay file when
# the tile takes its imagery, and warns about it as a layer of a combined
# provider over the tile, which the other layers serve.
api_key_code = """
import contextlib
import io
import os
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Imagery_Utils as IMG
lay = os.path.join(FNAMES.Provider_dir, "Test", "KEY.lay")
comb = os.path.join(FNAMES.Provider_dir, "KEYCOMB.comb")
with open(lay, "w") as f:
    f.write("grid_type=webmercator\\nurl_template=http://127.0.0.1:9/{zoom}/{x}/{y}.jpg?key={api_key}\\napi_key=\\n")
with open(comb, "w") as f:
    f.write("KEY global none medium\\nTEST global none low\\n")
try:
    IMG.initialize_extents_dict()
    IMG.initialize_color_filters_dict()
    IMG.initialize_providers_dict()
    IMG.initialize_combined_providers_dict()
finally:
    os.remove(lay)
    os.remove(comb)
tile = CFG.Tile(45, 5, "")
tile.zone_list = []
cases = [
    ("KEY", 0, "ERROR: Provider KEY needs an api_key in " + lay),
    ("KEYCOMB", 1, "WARNING: Layer KEY of KEYCOMB covers this tile"),
]
for (website, want, text) in cases:
    tile.default_website = website
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = IMG.initialize_local_combined_providers_dict(tile)
    if got != want or text not in out.getvalue():
        sys.exit(website + ": " + str(got) + ", " + repr(out.getvalue()) + ", want " + str(want) + " and " + repr(text))
"""

# A provider takes the tiles that its server answers with 404 or sends
# all white from its fallback, at the same zoomlevel. Ortho4XP rejects a
# provider whose fallback is missing, falls back itself, or is of another
# grid than the webmercator one, and a provider of another grid that names
# a fallback.
fallback_code = file_server + """
import contextlib
import io
import os
import numpy
from PIL import Image
import O4_File_Names as FNAMES
import O4_Imagery_Utils as IMG
def jpeg(color):
    f = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(f, "JPEG", quality = 95)
    return f.getvalue()
# FB serves the first tile of a box of 2 x 2 tiles, and the second all
# white, as IGN's server sends tiles near the edge of its imagery. FB2
# serves all four, in another color.
(zl, x, y) = (16, 10, 20)
box = [(x + i, y + j) for j in (0, 1) for i in (0, 1)]
colors = {"FB": (200, 60, 40), "FB2": (40, 60, 200)}
served = {
    "/FB/%d/%d/%d.jpg" % (zl, x, y): jpeg(colors["FB"]),
    "/FB/%d/%d/%d.jpg" % (zl, x + 1, y): jpeg((255, 255, 255)),
}
for (i, j) in box:
    served["/FB2/%d/%d/%d.jpg" % (zl, i, j)] = jpeg(colors["FB2"])
(url, paths, agents) = serve(served)
# The code of each provider, the line of its grid, its fallback, and
# whether it loads.
cases = [
    ("FB", "grid_type=webmercator", "FB2", True),
    ("FB2", "grid_type=webmercator", None, True),
    ("FB3", "request_type=tms", None, True),
    ("FB4", "grid_type=webmercator", "FB9", False),
    ("FB5", "grid_type=webmercator", "FB", False),
    ("FB6", "grid_type=webmercator", "FB3", False),
    ("FB7", "request_type=tms", "FB2", False),
]
lays = []
for (code, grid, fallback, loads) in cases:
    lays.append(os.path.join(FNAMES.Provider_dir, "Test", code + ".lay"))
    with open(lays[-1], "w") as f:
        f.write(grid + "\\nurl_template=" + url + code + "/{zoom}/{x}/{y}.jpg\\n")
        if fallback:
            f.write("fallback=" + fallback + "\\n")
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        IMG.initialize_extents_dict()
        IMG.initialize_color_filters_dict()
        IMG.initialize_providers_dict()
finally:
    for lay in lays:
        os.remove(lay)
errors = []
for (code, grid, fallback, loads) in cases:
    loaded = code in IMG.providers_dict
    warned = "Error in fallback for provider " + code + " :" in out.getvalue()
    if loaded != loads or warned == loads:
        errors.append(code + " (" + grid + ", fallback " + str(fallback) + "): loaded " + str(loaded) + ", warned " + str(warned) + ", want loaded " + str(loads))
if "FB" not in IMG.providers_dict:
    sys.exit("\\n".join(errors))
(ok, image) = IMG.build_texture_from_tilbox((x, y, x + 2, y + 2), zl, IMG.providers_dict["FB"])
for (i, j) in box:
    color = colors["FB" if (i, j) == (x, y) else "FB2"]
    tile = image.crop((256 * (i - x), 256 * (j - y), 256 * (i - x + 1), 256 * (j - y + 1)))
    mean = numpy.asarray(tile, dtype = float).mean(axis = (0, 1))
    if numpy.abs(mean - color).max() > 6:
        errors.append("tile " + str((zl, i, j)) + ": mean color " + str(mean.round(1)) + ", want " + str(color))
want = sorted(["/FB/%d/%d/%d.jpg" % (zl, i, j) for (i, j) in box] + ["/FB2/%d/%d/%d.jpg" % (zl, i, j) for (i, j) in box[1:]])
if not ok or sorted(paths) != want:
    errors.append("build_texture_from_tilbox: success " + str(ok) + ", requests " + str(sorted(paths)) + ", want 1 and " + str(want))
check_agents(agents)
if errors:
    sys.exit("\\n".join(errors))
"""

# Failures inside the steps: a task that raises fails its parallel launch
# while the others run, a texture whose build raises counts as failed, a
# stopped Step 3 leaves its DSF inactive, and so does one whose DSF fails
# or raises, beside a DSF left by an earlier stop. build_dsf leaves the
# active DSF in place while it writes the new one. A mask whose build
# raises fails Step 2.5. That last one leaves the tile without masks, so
# this check runs last.
failures_code = """
import os
import queue
import shutil
import threading
import O4_Config_Utils as CFG
import O4_File_Names as FNAMES
import O4_Imagery_Utils as IMG
import O4_Mask_Utils as MASK
import O4_Parallel_Utils as PAR
import O4_Tile_Utils as TILE
import O4_UI_Utils as UI
IMG.initialize_extents_dict()
IMG.initialize_color_filters_dict()
IMG.initialize_providers_dict()
IMG.initialize_combined_providers_dict()
threading.excepthook = lambda args: None
def fail(*args):
    raise ValueError("a test failure")
errors = []

tasks = queue.Queue()
done = []
def task(i):
    if i == 3:
        fail()
    done.append(i)
    return 1
for i in range(10):
    tasks.put((i,))
workers = PAR.parallel_launch(task, tasks, 2)
for _ in workers:
    tasks.put("quit")
if PAR.parallel_join(workers) or sorted(done) != [0, 1, 2, 4, 5, 6, 7, 8, 9]:
    errors.append("parallel_join with a task that raises: ran " + str(sorted(done)) + ", want 0 and the other tasks")

tile = CFG.Tile(45, 5, "")
tile.read_from_config()
build_jpeg_ortho = IMG.build_jpeg_ortho
IMG.build_jpeg_ortho = fail
textures = queue.Queue()
textures.put((0, 0, 16, "TEST"))
textures.put("quit")
failed = []
try:
    TILE.download_textures(tile, textures, queue.Queue(), failed)
except ValueError:
    pass
IMG.build_jpeg_ortho = build_jpeg_ortho
if failed != [(0, 0, 16, "TEST")]:
    errors.append("download_textures with a build that raises: failed " + str(failed) + ", want the texture")

stopped = CFG.Tile(45, 5, "")
stopped.read_from_config()
stopped.build_dir = os.path.abspath("stopped")
os.makedirs(stopped.build_dir)
shutil.copy(FNAMES.mesh_file(tile.build_dir, 45, 5), FNAMES.mesh_file(stopped.build_dir, 45, 5))
dsf = os.path.join(stopped.build_dir, "Earth nav data", "+40+000", "+45+005.dsf")
def stop(tile, download_queue):
    with open(dsf + ".tmp", "wb") as f:
        f.write(b"XPLNEDSF")
    UI.red_flag = True
    return 0
real_build_dsf = TILE.DSF.build_dsf
TILE.DSF.build_dsf = stop
TILE.skip_downloads = True
if TILE.build_tile(stopped) or os.path.exists(dsf):
    errors.append("Step 3 stopped: " + dsf + " in place, want it inactive")
for (how, build_dsf) in (("failing", lambda tile, download_queue: 0), ("raising", fail)):
    TILE.DSF.build_dsf = build_dsf
    if TILE.build_tile(stopped) or os.path.exists(dsf) or not os.path.exists(dsf + ".tmp"):
        errors.append("Step 3 with build_dsf " + how + " after a stop: " + dsf + " in place, want it inactive")
with open(dsf, "wb") as f:
    f.write(b"active")
UI.red_flag = False
built = real_build_dsf(stopped, queue.Queue())
with open(dsf, "rb") as f:
    active = f.read()
if not built or active != b"active" or not os.path.exists(dsf + ".tmp"):
    errors.append("build_dsf beside an active DSF: " + str(built) + ", the DSF " + repr(active[:16]) + ", want 1 and the DSF left as it was until Step 3 activates the new one")
shutil.rmtree(stopped.build_dir)

MASK.build_water_pre_mask = fail
if MASK.build_masks(tile):
    errors.append("build_masks with a mask that raises: succeeded")
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
import O4_File_Names as FNAMES
import O4_Tile_Utils as TILE
errors = []
gui = GUI.Ortho4XP_GUI()
gui.open_config_window()
# Load Tile Cfg on a config of 1.20, whose zones follow its zone_list.
zone = [[45.1, 5.1, 45.1, 5.2, 45.2, 5.2, 45.1, 5.1], 15, "TEST"]
build = FNAMES.build_dir(45, 5, "")
os.makedirs(build, exist_ok = True)
with open(os.path.join(build, "Ortho4XP_+45+005.cfg"), "w") as f:
    f.write("zone_list=[]\\nzone_list.append(" + str(zone) + ")\\n")
gui.lat.set("45")
gui.lon.set("5")
gui.config_window.load_tile_cfg()
if gui.config_window.v_["zone_list"].get() != str([zone]):
    errors.append("Load Tile Cfg: zone_list " + gui.config_window.v_["zone_list"].get() + ", want " + str([zone]))
gui.config_window.v_["skip_downloads"].set(sys.argv[1])
gui.config_window.v_["masks_width"].set("[1, 2")
gui.config_window.v_["mesh_zl"].set("17")
gui.config_window.v_["use_masks_for_inland"].set("True")
gui.config_window.v_["custom_bathymetry"].set("floor.tif")
gui.config_window.v_["water_tech"].set("XP11 + bathy")
gui.config_window.apply_changes()
if TILE.skip_downloads is not False or CFG.masks_width != 100:
    errors.append("config window: kept invalid values")
if (CFG.get_var("mesh_zl") != 17 or CFG.get_var("use_masks_for_inland") is not True
        or CFG.get_var("custom_bathymetry") != "floor.tif" or CFG.get_var("water_tech") != "XP11 + bathy"):
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
    "python", "Tiles", "tmp", "venv", "yOrtho4XP_Overlays",
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
# returns them as errors. Its standard error goes through a file: after a
# timeout, subprocess.run on Windows reads a pipe until each program that
# holds it ends, such as a hung one that the check started.
def check(name, code, args, work, top, log, timeout = 300):
    path = os.path.join(work, name + ".py")
    with open(path, "w", encoding = "utf-8") as f:
        f.write("import sys\nsys.dont_write_bytecode = True\nsys.path[:0] = ['src', 'Providers']\n" + code)
    with open(log, "ab") as out, tempfile.TemporaryFile() as e:
        try:
            status = subprocess.run([sys.executable, path] + args, cwd = top, env = offline_env(), stdout = out, stderr = e, timeout = timeout).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
        e.seek(0)
        err = e.read().decode("utf-8", "replace")
        out.write(err.encode("utf-8"))
    if status == 0:
        return []
    return [name + ": " + line for line in err.splitlines() if line.strip()] or [name + ": exit status " + str(status) + ", see " + log]

# The patches of a DSF that DSFTool printed in text, by terrain, as
# (near, far, flags, depth, vertices).
def dsf_patches(text):
    terrains = []
    out = {}
    with open(text, encoding = "latin-1") as f:
        for line in f:
            w = line.split()
            if not w:
                continue
            if w[0] == "TERRAIN_DEF":
                terrains.append(w[1])
            elif w[0] == "BEGIN_PATCH":
                patch = (float(w[2]), float(w[3]), int(w[4]), int(w[5]), [])
                out.setdefault(terrains[int(w[1])], []).append(patch)
            elif w[0] == "PATCH_VERTEX":
                patch[4].append([float(x) for x in w[1:]])
    return out

# Every triangle of a DSF turns clockwise in lon and lat, as build_dsf
# writes them, once its vertices are quantized in their pools. The others
# lie flat or turned over. DSFTool prints positions to 1e-9 degree, close
# enough to take them back exactly to the steps of a pool at level 8,
# which hold the steps of every coarser pool.
def check_winding(text, patches):
    step = 2 ** -8 / 65535
    for (name, ps) in patches.items():
        for (near, far, flags, depth, vertices) in ps:
            for k in range(0, len(vertices), 3):
                tri = [tuple(v[:2]) for v in vertices[k : k + 3]]
                (a, b, c) = [(round((x - lon) / step), round((y - lat) / step)) for (x, y) in tri]
                if (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]) >= 0:
                    return [text + ": " + name + ": a triangle on " + str(tri) + ", want it turning clockwise"]
    return []

# r, of 1201 by 1201 posts from the south west corner of the tile, at lon,
# lat, by bilinear interpolation.
def bilinear(r, lon_, lat_):
    (x, y) = ((lon_ - lon) * 1200, (lat_ - lat) * 1200)
    (i, j) = (min(int(x), 1199), min(int(y), 1199))
    (fx, fy) = (x - i, y - j)
    return ((1 - fx) * (1 - fy) * r[j, i] + fx * (1 - fy) * r[j, i + 1]
        + (1 - fx) * fy * r[j + 1, i] + fx * fy * r[j + 1, i + 1])

# The fetch of water vertices, the plane after their normals, is the depth
# of the floor over 50 m, between 0 and 1, from the rasters of the DSF.
def check_fetch(text, patches, names):
    e = numpy.fromfile(text + ".elevation.raw", "<i2").astype(float).reshape(1201, 1201)
    f = numpy.fromfile(text + ".sea_level.raw", "<i2").astype(float).reshape(1201, 1201)
    count = 0
    for (name, ps) in patches.items():
        if not any(name.endswith(n) for n in names):
            continue
        for (near, far, flags, depth, vertices) in ps:
            for v in vertices:
                want = min(max((bilinear(e, v[0], v[1]) - bilinear(f, v[0], v[1])) / 50, 0), 1)
                count += 1
                if abs(v[5] - want) > 2e-5:
                    return [text + ": " + name + ": fetch " + str(v[5]) + " at " + str(v[:2]) + ", want " + str(round(want, 6))]
    if not count:
        return [text + ": no vertices of " + str(names)]
    return []

# The depth plane of the physical water patches of names, after the
# fetch: 0 at a position that a patch of land shares, the shore, and 1
# elsewhere.
def check_depth(what, patches, names):
    water = lambda name: name == "terrain_Water" or "_sea" in name or "_water" in name
    land = {tuple(v[:2]) for (name, ps) in patches.items() if not water(name) for p in ps for v in p[4]}
    for (name, ps) in patches.items():
        if not any(name.endswith(n) for n in names):
            continue
        for (near, far, flags, depth, vertices) in ps:
            for v in vertices:
                want = 0 if tuple(v[:2]) in land else 1
                if v[6] != want:
                    return [what + ": " + name + ": depth " + str(v[6]) + " at " + str(v[:2]) + ", want " + str(want)]
    return []

# The water of XP12, the main build: the masked sea is physical
# WATER_COLOR_MASK terrain, whose vertices carry fetch, depth, and the
# texture coordinates after their normals. The lake and the river, in ZL 12
# textures below mask_zl, are masked too, from the masks under their
# textures. The colours of those textures, mostly land, are checked at
# points: X-Plane's water in the sea and the lake, the photo on land.
low_textures = {
    "1472_2096_TEST12.dds": [((45.01, 5.01), True), ((45.05, 5.5), False)],
    "1456_2096_TEST12.dds": [((45.846, 5.608), True), ((45.5, 5.3), False)],
    "1456_2112_TEST12.dds": [((45.7, 5.9), False)],
}

# The RGB at lat, lon of the texture named name, at ZL zl, in build.
def texel(build, name, zl, lat, lon):
    (til_y, til_x) = (int(v) for v in name.split("_")[:2])
    with Image.open(os.path.join(build, "textures", name)) as im:
        n = im.size[0] / 16
        x = ((lon + 180) / 360 * 2 ** zl - til_x) * n
        y = ((1 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2 * 2 ** zl - til_y) * n
        return numpy.array(im.convert("RGB").getpixel((int(x), int(y))), numpy.int16)

def check_xp12(text, build, served):
    errors = []
    terrain = os.path.join(build, "terrain")
    ters = sorted(os.listdir(terrain))
    sea = [t for t in ters if t.endswith("_sea.ter")]
    if not sea:
        errors.append("xp12: no _sea.ter, want the masked sea as WATER_COLOR_MASK terrain")
    for t in sea:
        with open(os.path.join(terrain, t)) as f:
            lines = f.read().split("\n")
        if "WATER_COLOR_MASK" not in lines or any(l.startswith("BORDER_TEX") for l in lines):
            errors.append("xp12: " + t + ": " + repr(lines) + ", want WATER_COLOR_MASK and no BORDER_TEX")
    if [t for t in ters if t.endswith("_sea_overlay.ter")]:
        errors.append("xp12: a _sea_overlay.ter, want none with the masks in the textures")
    if [t for t in ters if t.endswith("_water_overlay.ter")]:
        errors.append("xp12: " + str([t for t in ters if t.endswith("_water_overlay.ter")]) + ", want the inland water masked")
    # Their textures fade from the photo into the colour of X-Plane's water,
    # here that of any.png, 13, 32, 51, as DXT1, which holds it within 2
    # levels.
    for t in sea:
        with open(os.path.join(terrain, t)) as f:
            dds = [line.split()[1] for line in f if line.startswith("BASE_TEX_NOWRAP")][0]
        dds = os.path.normpath(os.path.join(terrain, dds))
        with open(dds, "rb") as f:
            fourcc = f.read(88)[84:]
        if os.path.basename(dds) in low_textures:
            for ((lat, lon), wet) in low_textures[os.path.basename(dds)]:
                got = texel(build, os.path.basename(dds), 12, lat, lon)
                want = numpy.array((13, 32, 51)) if wet else served
                if numpy.abs(got - want).max() > (2 if wet else 8):
                    errors.append("xp12: " + dds + ": " + str(got.tolist()) + " at " + str((lat, lon)) + ", want " + ("X-Plane's water" if wet else "the photo") + " " + str(numpy.round(want).tolist()))
            continue
        a = numpy.asarray(Image.open(dds).convert("RGB").reduce(8), numpy.int16)
        water = (numpy.abs(a - (13, 32, 51)).max(axis = 2) <= 2).mean()
        if fourcc != b"DXT1" or water < 0.1:
            errors.append("xp12: " + dds + ": " + fourcc.decode("latin-1") + " with " + str(round(100 * water, 1)) + "% of the colour of X-Plane's water, want DXT1 and 10% or more")
    for name in low_textures:
        if name[:-4] + "_sea.ter" not in sea:
            errors.append("xp12: no " + name[:-4] + "_sea.ter, want the water of that ZL 12 texture masked")
    patches = dsf_patches(text)
    for (name, ps) in patches.items():
        if name.endswith("_sea.ter"):
            want = (1, 9)
        elif name == "terrain_Water":
            want = (1, 7)
        else:
            continue
        for (near, far, flags, depth, vertices) in ps:
            if (flags, depth) != want or far != -1:
                errors.append("xp12: " + name + ": a patch with flags " + str(flags) + ", depth " + str(depth) + ", and far LOD " + str(far) + ", want " + str(want) + " and -1")
                break
            # Water is flat, as in Laminar's DSFs.
            tilted = [v for v in vertices if abs(v[3]) > 1e-4 or abs(v[4]) > 1e-4]
            if tilted:
                errors.append("xp12: " + name + ": normal " + str(tilted[0][3:5]) + " at " + str(tilted[0][:2]) + ", want 0, 0")
                break
    errors += check_winding(text, patches)
    ((lat0, lon0), (lat1, lon1)) = (osm["water"][2][0][0], osm["water"][2][0][2])
    pond = [v for (name, ps) in patches.items() if name.endswith("_sea.ter") for p in ps for v in p[4]
        if lon0 < v[0] < lon1 and lat0 < v[1] < lat1]
    if not pond:
        errors.append("xp12: no vertex of WATER_COLOR_MASK in the pond, want its inland water masked")
    ((lat0, lon0), (lat1, lon1)) = (osm["water"][0][0][0], osm["water"][0][0][2])
    lake = [v for (name, ps) in patches.items() if name.endswith("_sea.ter") for p in ps for v in p[4]
        if lon0 < v[0] < lon1 and lat0 < v[1] < lat1]
    if not lake:
        errors.append("xp12: no vertex of WATER_COLOR_MASK in the lake, want it masked")
    errors += check_depth("xp12", patches, ("_sea.ter", "terrain_Water"))
    return errors + check_fetch(text, patches, ("_sea.ter", "terrain_Water"))

# The water of XP11 + bathy, the variant: the masked sea is overlays of
# imprinted DXT5 textures over physical terrain_Water, and the lake an
# overlay whose BORDER_TEX is water_transition.png. The depth plane of
# terrain_Water is 0 at the shore and 1 elsewhere, and its patches have no
# far LOD and flat normals. The coast of the ZL 12 texture of the south
# west, below mask_zl, takes a mask too: the unmasked lakes leave the masks
# of Step 2.5 complete.
def check_variant(top, build):
    errors = []
    terrain = os.path.join(build, "terrain")
    ters = sorted(os.listdir(terrain))
    sea = [t for t in ters if t.endswith("_sea_overlay.ter")]
    if not sea or [t for t in ters if t.endswith("_sea.ter")]:
        errors.append("variant: terrain " + str([t for t in ters if "_sea" in t]) + ", want the masked sea in overlays alone")
    if not [t for t in ters if t.endswith("_water_overlay.ter")]:
        errors.append("variant: no _water_overlay.ter for the lake")
    for t in sea:
        with open(os.path.join(terrain, t)) as f:
            lines = f.read().split("\n")
        dds = os.path.normpath(os.path.join(terrain, [l.split()[1] for l in lines if l.startswith("BASE_TEX_NOWRAP")][0]))
        with open(dds, "rb") as f:
            fourcc = f.read(88)[84:]
        if [l for l in lines if l.startswith("BORDER_TEX")] or fourcc != b"DXT5":
            errors.append("variant: " + t + ": " + fourcc.decode("latin-1") + ", want the mask imprinted in a DXT5 and no BORDER_TEX")
            break
    text = os.path.join(top, "variant.txt")
    patches = dsf_patches(text)
    for (name, ps) in patches.items():
        want = (2, 9) if name.endswith("_overlay.ter") else (1, 7) if name == "terrain_Water" else None
        if want and any((flags, depth) != want for (near, far, flags, depth, vertices) in ps):
            errors.append("variant: " + name + ": a patch with other flags and planes than " + str(want))
    water = patches.get("terrain_Water", [])
    if not water:
        errors.append("variant: no terrain_Water, want it under the overlays of the sea")
    for (near, far, flags, depth, vertices) in water:
        tilted = [v for v in vertices if abs(v[3]) > 1e-4 or abs(v[4]) > 1e-4]
        if far != -1 or tilted:
            errors.append("variant: terrain_Water: far LOD " + str(far) + (" and normal " + str(tilted[0][3:5]) if tilted else "") + ", want -1 and 0, 0")
            break
    if "1472_2096_TEST12_sea_overlay.ter" not in ters:
        errors.append("variant: no 1472_2096_TEST12_sea_overlay.ter, want the coast of that ZL 12 texture masked")
    errors += check_winding(text, patches)
    errors += check_depth("variant", patches, ("terrain_Water",))
    return errors + check_fetch(text, patches, ("terrain_Water",))

# The rasters of the built DSF, which DSFTool writes beside the text, as
# text.<name>.raw: the soundscape and the season of the Global Scenery,
# and the elevation and sea_level that Step 3 computes from the mesh.
def check_rasters(text, gs, vertices, triangles):
    errors = []
    for name in ("soundscape", "spr1"):
        with open(os.path.join(gs, name + ".raw"), "rb") as f, open(text + "." + name + ".raw", "rb") as g:
            if f.read() != g.read():
                errors.append(text + "." + name + ".raw: differs from the Global Scenery's")
    e = numpy.fromfile(text + ".elevation.raw", "<i2").astype(int).reshape(1201, 1201)
    f = numpy.fromfile(text + ".sea_level.raw", "<i2").astype(int).reshape(1201, 1201)
    # The mesh at posts picked at random, by brute force.
    v = numpy.array(vertices)
    t = numpy.array(triangles) - 1
    (x, y, z) = ((v[:, 0] - lon) * 1200, (v[:, 1] - lat) * 1200, v[:, 2] * 100000)
    (a, b, c) = (t[:, 0], t[:, 1], t[:, 2])
    d = (y[b] - y[c]) * (x[a] - x[c]) + (x[c] - x[b]) * (y[a] - y[c])
    for (i, j) in numpy.random.default_rng(1).integers(0, 1201, (40, 2)):
        l0 = ((y[b] - y[c]) * (i - x[c]) + (x[c] - x[b]) * (j - y[c])) / d
        l1 = ((y[c] - y[a]) * (i - x[c]) + (x[a] - x[c]) * (j - y[c])) / d
        l2 = 1 - l0 - l1
        k = numpy.flatnonzero((l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9))[0]
        want = l0[k] * z[a[k]] + l1[k] * z[b[k]] + l2[k] * z[c[k]]
        if abs(e[j, i] - want) > 1:
            errors.append(text + ".elevation.raw: " + str(e[j, i]) + " at post " + str((i, j)) + ", want the " + str(round(want, 1)) + " of the mesh")
            break
    # The floor lies 1 m below land and at least 1 m below water, 50 m
    # below the open sea at the south west corner, and at the centre of the
    # lake of 1.25 by 1.3 km, about 8 posts from its shore, 29 m below.
    if (f > e - 1).any():
        errors.append(text + ".sea_level.raw: above the elevation minus 1 m at " + str(int((f > e - 1).sum())) + " posts")
    if (e[0, 0], f[0, 0]) != (0, -50):
        errors.append(text + ": elevation and sea_level " + str((e[0, 0], f[0, 0])) + " at the south west corner, in the sea, want 0 and -50")
    (i, j) = (round(0.608 * 1200), round(0.846 * 1200))
    if not -40 <= f[j, i] - e[j, i] <= -20:
        errors.append(text + ": sea_level " + str(f[j, i] - e[j, i]) + " m from the surface at the centre of the lake, want -40 to -20")
    return errors

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
        f.write("custom_overlay_src=" + os.path.relpath(gs, top) + "\n")
        f.write("custom_scenery_dir=" + scenery_dir + "\n")
    no_gs = check("globalscenery", global_scenery_code, [gs], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += no_gs
    errors += check("rasters", rasters_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("waterpoints", water_points_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("watercolour", water_colour_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("masks", masks_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("window", window_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("antimeridian", antimeridian_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("readmesh", read_mesh_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("completemasks", complete_masks_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("assembly", assembly_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("triangle4xp", triangle4xp_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("splits", splits_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("split", split_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    errors += check("recut", recut_code, [], work, top, os.path.join(work, "Ortho4XP.log"))
    # The build reads the Global Scenery that globalscenery makes.
    if no_gs:
        return errors

    log = os.path.join(work, "Ortho4XP.log")
    german = os.path.join(work, "german.py")
    with open(german, "w") as f:
        f.write(german_code)
    status = run([sys.executable, german, "Ortho4XP.py", str(lat), str(lon)], top, log, 900)
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
    # The fake Global Scenery lies outside an X-Plane, without its maps of
    # the colour of the water.
    if "the maps of the colour of X-Plane's water are missing" not in out:
        errors.append("Step 3: no warning for the missing maps of the colour of X-Plane's water")
    # The live check of the providers fails on a provider that Ortho4XP
    # rejects.
    r = subprocess.run([sys.executable, os.path.join("Utils", "run", "providers.py"), "EVIL"], cwd = top, env = offline_env(), capture_output = True, text = True, timeout = 300)
    if r.returncode != 1 or "EVIL fail" not in r.stdout:
        errors.append("providers.py EVIL: status " + str(r.returncode) + ", output " + repr((r.stdout + r.stderr)[-300:]) + ", want 1 and EVIL fail")
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
    i = lines.index("Triangles")
    triangles = [[int(x) for x in line.split()[:3]] for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    # Step 1 adds water points, nodes on no edge, on interior posts of the
    # even lattice in the sea or the lake, which the mesh keeps.
    with open(os.path.join(build, "Data" + tile + ".node")) as f:
        nodes = {int(w[0]): (float(w[1]), float(w[2])) for w in (line.split() for line in f.read().split("\n")[1:]) if len(w) > 2}
    with open(os.path.join(build, "Data" + tile + ".poly")) as f:
        poly = f.read().split("\n")
    on_edges = {int(n) for line in poly[3:3 + int(poly[2].split()[0])] for n in line.split()[1:3]}
    posts = [(x * 1200, y * 1200) for (k, (x, y)) in nodes.items() if k not in on_edges]
    boxes = [(way[0], way[2]) for (way, tags) in osm["water"]]
    in_lake = lambda i, j: any(lon0 < 5 + i / 1200 < lon1 and lat0 < 45 + j / 1200 < lat1 for ((lat0, lon0), (lat1, lon1)) in boxes)
    kept = {(round((lon - 5) * 1200, 3), round((lat - 45) * 1200, 3)) for (lon, lat, z) in vertices}
    bad = [(i, j) for (i, j) in posts if abs(i - round(i)) > 1e-3 or abs(j - round(j)) > 1e-3
        or round(i) % 2 or round(j) % 2 or not (0 < i < 1200 and 0 < j < 1200)
        or not (i + j < 300 or in_lake(i, j)) or (round(i, 3), round(j, 3)) not in kept]
    if bad or not [1 for (i, j) in posts if i + j < 300] or not [1 for (i, j) in posts if in_lake(i, j)]:
        errors.append("Step 1: " + str(len(posts)) + " water points, " + str(bad[:3]) + " off the lattice, out of the water, or out of the mesh, want some in the sea and the lake")
    # Step 2 splits the water triangles whose vertices all lie on the shore
    # and the bridges between two shores, and meshes again, which leaves
    # few of them.
    types = [int(line.split()[3]) & 7 for line in lines[i + 2:i + 2 + int(lines[i + 1])]]
    in_wet = {v for (t, k) in zip(triangles, types) if k for v in t}
    shore = in_wet & {v for (t, k) in zip(triangles, types) if not k for v in t}
    wet_edges = collections.Counter(tuple(sorted((t[a], t[(a + 1) % 3]))) for (t, k) in zip(triangles, types) if k for a in range(3))
    left = (sum(1 for (t, k) in zip(triangles, types) if k and set(t) <= shore)
        + sum(1 for (e, c) in wet_edges.items() if c == 2 and set(e) <= shore))
    split = re.search(r"Splitting (\d+) beached water triangles and (\d+) bridges", out)
    if not split or 10 * left >= int(split[1]) + int(split[2]):
        errors.append("Step 2: " + (split[0] if split else "no splits") + ", and " + str(left) + " left in the mesh, want splits and a tenth of them left at most")
    top_alt = 100000 * max(v[2] for v in vertices)
    if not 1150 < top_alt < 1201:
        errors.append(mesh + ": highest vertex at " + str(round(top_alt)) + " m, want the 1200 m of the hill")
    # The vertices of the ramp follow its plane profile.
    ((lat0, lon0), (lat1, lon1)) = (ramp[0], ramp[2])
    on_ramp = [(lon, 100000 * z) for (lon, lat, z) in vertices if lon0 <= lon <= lon1 and lat0 <= lat <= lat1]
    off = [(lon, z) for (lon, z) in on_ramp if abs(z - (450 - 30 * (lon - lon0) / (lon1 - lon0))) > 0.5]
    if len(on_ramp) < 10 or off:
        errors.append("ramp.patch.osm: " + str(len(off)) + " of " + str(len(on_ramp)) + " vertices off the profile, such as " + str(off[:3]))
    # The rejected patches add no vertices: their boxes, 1.5 km long and
    # 67 m wide on the plateau, where the mesh is coarse, hold none.
    for name in ("open", "zero"):
        ((lat0, lon0), (lat1, lon1)) = (patches[name][0][0][0], patches[name][0][0][2])
        inside = [z for (lon, lat, z) in vertices if lon0 <= lon <= lon1 and lat0 <= lat <= lat1]
        if inside:
            errors.append(name + ".patch.osm: " + str(len(inside)) + " vertices in its box, want none")
    legacy = os.path.join(work, "legacy.cfg")
    with open(legacy, "w", encoding = "utf-8-sig") as f:
        for zone in config["zone_list"] + [payload]:
            f.write("zone_list.append(" + str(zone) + ")\n")
        f.write("use_masks_for_inland=1\nhmin=0\ndistance_masks_too=True\nratio_bathy=0.5\n")
        f.write("water_tech=XP13\nmasking_mode=Sand\n")
    errors += check("legacy", legacy_code, [legacy, repr(config["zone_list"]), scenery_dir], work, top, log)
    errors += check("7z", seven_zip_code, [], work, top, log)
    errors += check("link", link_code, [], work, top, log)
    errors += check("apt", apt_code, [], work, top, log)
    errors += check("geotiff", geotiff_code, [os.path.join(top, config["custom_dem"])], work, top, log)
    errors += check("opentopography", opentopography_code, [], work, top, log)
    errors += check("viewfinder", viewfinder_code, [], work, top, log)
    errors += check("community", community_code, [], work, top, log)
    errors += check("unwanted", unwanted_code, [], work, top, log)
    errors += check("demo", demo_code, [], work, top, log)
    errors += check("neighbours", neighbours_code, [], work, top, log)
    errors += check("extent", extent_code, [payload], work, top, log)
    if os.path.exists(os.path.join(top, "pwned")):
        errors.append("a config or provider file ran code")
    textures = sorted(glob.glob(os.path.join(build, "textures", "*.dds")))
    formats = {}
    for path in textures:
        with open(path, "rb") as f:
            formats.setdefault(f.read(88)[84:].decode("latin-1"), []).append(path)
    if sorted(formats) != ["DXT1"]:
        errors.append("textures: formats " + str(sorted(formats)) + ", want DXT1, as XP12 water fades masked textures")
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
    # The textures of the masked sea fade into the colour of the water.
    faded = {os.path.join(build, "textures", t[:-len("_sea.ter")] + ".dds") for t in os.listdir(os.path.join(build, "terrain")) if t.endswith("_sea.ter")}
    for path in [p for p in formats.get("DXT1", []) if p not in faded][:1]:
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
    ters = glob.glob(os.path.join(build, "terrain", "*.ter"))
    if not ters:
        errors.append("terrain: no .ter files")
    # The decal of use_decal_on_terrain goes on land, and the water gets
    # none.
    decals = []
    for path in ters:
        with open(path) as f:
            if "DECAL_LIB" in f.read():
                decals.append(os.path.basename(path))
    wet = sorted(t for t in decals if "_sea" in t or "_water" in t)
    if not decals or wet:
        errors.append("terrain: decals on " + str(len(decals)) + " files, " + str(len(wet)) + " of water such as " + str(wet[:2]) + ", want them on land only")

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
        defs = [line[len("RASTER_DEF "):] for line in out.split("\n") if line.startswith("RASTER_DEF ")]
        if "PROPERTY sim/west 5" not in out or defs != ["elevation", "sea_level", "soundscape", "spr1"]:
            errors.append(text + ": rasters " + str(defs) + ", want elevation, sea_level, soundscape, and spr1, and sim/west 5")
        else:
            errors += check_rasters(text, gs, vertices, triangles)
            errors += check_xp12(text, build, served)
    errors += check("pond", pond_code, [], work, top, log)
    errors += check("rebuild", rebuild_code, [], work, top, log)
    variant = check("variant", variant_code, [], work, top, log)
    errors += variant or check_variant(top, build)
    errors += check("streamer", streamer_code, [], work, top, log)
    errors += check("lakemask", lake_mask_code, [], work, top, log)
    bathymetry = check("bathymetry", bathymetry_code, [], work, top, log)
    errors += bathymetry or check_bathymetry(top, work)
    errors += check("overlay", overlay_code, [gs], work, top, log)
    errors += check("norasters", no_rasters_code, [os.path.join(work, "Global Scenery 11")], work, top, log)
    errors += check("badoverlay", bad_overlay_code, [os.path.join(work, "Global Scenery bad")], work, top, log)

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

    # The command line takes the tiles that the GUI takes, those of the Web
    # Mercator grid, from 85 S to 85 N and from 180 W to 180 E, and stops
    # on one beyond with the range.
    for args in (["-90", "0", "BI", "16"], ["85", "0"], ["45", "180"]):
        r = subprocess.run([sys.executable, "Ortho4XP.py"] + args, cwd = top, env = offline_env(), capture_output = True, text = True, timeout = 300)
        if r.returncode != 2 or "out of range" not in r.stderr:
            errors.append("Ortho4XP.py " + " ".join(args) + ": status " + str(r.returncode) + ", stderr " + repr(r.stderr[-300:]) + ", want 2 and the range")
    errors += check("stop", stop_code, [], work, top, log)
    errors += check("customosm", custom_osm_code, [], work, top, log)
    errors += check("providerfiles", provider_files_code, [], work, top, log)
    errors += check("apikey", api_key_code, [], work, top, log)
    errors += check("fallback", fallback_code, [], work, top, log)
    errors += check("failures", failures_code, [], work, top, log)
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
    # Each malformed mesh, with the error that moulinette gives for it.
    bad = [
        ("a vertex list cut short", small_mesh.replace("5.1 45.2 0.003 0\n5.2 45.2 0.004 0\n", ""), "malformed vertex"),
        ("a normal of one number", small_mesh.replace("0.3 0.4 0\n0.3 0.4 0\n", "0.3 0.4 0\n0.3\n"), "malformed normal"),
        ("a triangle list cut short", small_mesh.replace("Triangles\n2\n", "Triangles\n3\n"), "incomplete"),
        ("a triangle of three numbers", small_mesh.replace("2 4 3 1\n", "2 4 3\n"), "incomplete"),
        ("a line of 300 bytes", small_mesh.replace("5.2 45.1 0.002 0", "5.2 45.1 0.002 0" + " " * 300), "line too long"),
        ("a triangle on a missing vertex", small_mesh.replace("2 4 3 1\n", "2 5 3 1\n"), "missing vertex"),
        ("a vertex count beyond int", small_mesh.replace("Vertices\n4\n", "Vertices\n2147483647\n"), "too large"),
        ("a triangle count beyond int", small_mesh.replace("Triangles\n2\n", "Triangles\n4294967298\n"), "too large"),
        ("a negative normal count", small_mesh.replace("Normals\n4\n", "Normals\n-4\n"), "negative count"),
        ("a vertex number beyond int", small_mesh.replace("2 4 3 1\n", "2 4294967300 3 1\n"), "incomplete"),
        ("100 triangles on one edge", small_mesh.replace("Triangles\n2\n1 2 3 1\n2 4 3 1\n", "Triangles\n100\n" + "1 2 3 1\n1 3 2 1\n" * 50), "share an edge"),
    ]
    for (what, text, message) in bad:
        with open(mesh, "w") as f:
            f.write(text)
        r = subprocess.run([moulinette, "0", mesh], cwd = work, stdout = subprocess.PIPE, timeout = 60)
        with open(log, "ab") as f:
            f.write(r.stdout)
        with open(mesh) as f:
            if r.returncode != 1 or message.encode() not in r.stdout or f.read() != text:
                errors.append("moulinette on a mesh with " + what + ": status " + str(r.returncode) + ", want 1, the error " + repr(message) + ", and the mesh as it was")
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
            if r.returncode != 1 or f.read() != small_mesh or os.path.exists(mesh + ".tmp"):
                errors.append("moulinette with a write that fails: status " + str(r.returncode) + ", want 1, the mesh as it was, and no .tmp")
    # A rename that fails, here over a folder.
    folder = os.path.join(work, "folder.mesh")
    os.makedirs(folder)
    with open(mesh, "w") as f:
        f.write(small_mesh)
    status = run([moulinette, "0", mesh, folder], work, log, 60)
    if status != 1 or os.path.exists(folder + ".tmp"):
        errors.append("moulinette with a rename that fails: status " + str(status) + ", want 1 and no .tmp")
    status = run([moulinette, "0"], work, log, 60)
    if status != 2:
        errors.append("moulinette without a mesh: status " + str(status) + ", want 2")
    with open(mesh, "w") as f:
        f.write(small_mesh)
    status = run([moulinette, "x", mesh], work, log, 60)
    if status != 2:
        errors.append("moulinette at zoomlevel x: status " + str(status) + ", want 2")
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
