import glob
import math
import time
import os
import shutil
import sys
import subprocess
import numpy
from PIL import Image
import O4_File_Names as FNAMES
import O4_UI_Utils as UI

# the following is meant to be modified directly by users who need it (in the 
# config window, not here!)
ovl_exclude_pol = [0]
ovl_exclude_net = []

# the following is meant to be modified by the CFG module at run time
custom_overlay_src = ""

if "dar" in sys.platform:
    dsftool_cmd = os.path.join(FNAMES.Utils_dir, "mac", "DSFTool ")
    seven_zip_bundled = os.path.join(FNAMES.Utils_dir, "mac", "7zz")
elif "win" in sys.platform:
    dsftool_cmd = os.path.join(FNAMES.Utils_dir, "win", "DSFTool.exe ")
    seven_zip_bundled = os.path.join(FNAMES.Utils_dir, "win", "7z.exe")
else:
    dsftool_cmd = os.path.join(FNAMES.Utils_dir, "lin", "DSFTool ")
    seven_zip_bundled = os.path.join(FNAMES.Utils_dir, "lin", "7zz")


################################################################################
def seven_zip():
    # The 7-Zip command: the copy in Utils, else 7zz, 7z, or 7za on the
    # PATH, else the one the Windows installer puts in Program Files, which
    # ProgramW6432 names for a 32-bit Python.
    if os.path.isfile(seven_zip_bundled) and os.access(
        seven_zip_bundled, os.X_OK
    ):
        return seven_zip_bundled
    for name in ("7zz", "7z", "7za"):
        path = shutil.which(name)
        if path:
            return path
    if sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramW6432"):
            path = os.path.join(
                os.environ.get(var, "C:\\Program Files"), "7-Zip", "7z.exe"
            )
            if os.path.isfile(path):
                return path
    return None


################################################################################
def extract_7z(archive, dest):
    # Extracts the files of a 7z archive into dest. Returns 1 on success,
    # and 0 after a message otherwise.
    cmd = seven_zip()
    if not cmd:
        UI.lvprint(
            0,
            "   ERROR: 7-Zip not found. Install it (the package 7zip or",
            "p7zip-full on Linux, sevenzip with Homebrew, or from",
            "https://www.7-zip.org), or use a release archive of Ortho4XP.",
        )
        return 0
    try:
        r = subprocess.run(
            [cmd, "e", "-y", "-o" + dest, archive],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        UI.lvprint(0, "   ERROR: Could not run", cmd + ":", e)
        return 0
    if r.returncode:
        UI.lvprint(
            0,
            "   ERROR: 7-Zip could not extract",
            archive + ":",
            r.stderr.decode(errors="replace").strip(),
        )
        return 0
    return 1

################################################################################
def global_scenery_dsf(lat, lon):
    # The path of the DSF of the tile in the Global Scenery, or in the
    # X-Plane 12 Demo Areas beside it when they hold the tile: X-Plane
    # loads those first, and some tiles come only there.
    name = os.path.join("Earth nav data", FNAMES.long_latlon(lat, lon) + ".dsf")
    if custom_overlay_src:
        demo = os.path.join(
            os.path.dirname(os.path.normpath(custom_overlay_src)),
            "X-Plane 12 Demo Areas",
            name,
        )
        if os.path.exists(demo):
            return demo
    return os.path.join(custom_overlay_src, name)


################################################################################
# The colour of X-Plane 12's water where it has no map of it, that of
# Resources/bitmaps/world/water/any.png.
open_sea_colour = (13, 32, 51)
# The maps of water_colour_map by tile, which Step 3 empties as it starts.
water_colour_maps = {}


def water_colour_dir():
    # The folder of X-Plane 12's maps of the colour of its water, in the
    # X-Plane that holds the Global Scenery of custom_overlay_src, or None.
    if not custom_overlay_src:
        return None
    root = os.path.dirname(os.path.dirname(os.path.normpath(custom_overlay_src)))
    return os.path.join(root, "Resources", "bitmaps", "world", "water")


def water_colour_map(lat, lon):
    # X-Plane 12's map of the colour of its water over the tile at lat,
    # lon, as an array of RGB bytes in rows from the north, or None.
    if (lat, lon) not in water_colour_maps:
        folder = water_colour_dir()
        name = folder and os.path.join(
            folder,
            FNAMES.round_latlon(lat, lon),
            FNAMES.short_latlon(lat, lon) + ".png",
        )
        rgb = None
        if name and os.path.isfile(name):
            try:
                rgb = numpy.asarray(Image.open(name).convert("RGB"))
            except OSError as e:
                UI.vprint(1, "   WARNING: could not read", name + ":", e)
        water_colour_maps[(lat, lon)] = rgb
    return water_colour_maps[(lat, lon)]


def water_colour(lat, lon):
    # The colour of X-Plane 12's own water at lat, lon, numbers or arrays
    # that broadcast together, sampled bilinearly from the centres of the
    # pixels of its maps, as RGB along a last axis.
    (lat, lon) = numpy.broadcast_arrays(
        numpy.asarray(lat, float), numpy.asarray(lon, float)
    )
    # 180 E is the west edge of the tiles at 180 W.
    lon = numpy.where(lon >= 180, lon - 360, lon)
    out = numpy.empty(lat.shape + (3,))
    (tlat, tlon) = (numpy.floor(lat), numpy.floor(lon))
    tiles = numpy.stack((tlat.ravel(), tlon.ravel()), axis=1)
    for (a, b) in numpy.unique(tiles, axis=0):
        at = (tlat == a) & (tlon == b)
        rgb = water_colour_map(int(a), int(b))
        if rgb is None:
            out[at] = open_sea_colour
            continue
        (h, w) = rgb.shape[:2]
        x = numpy.clip((lon[at] - b) * w - 0.5, 0, w - 1)
        y = numpy.clip((a + 1 - lat[at]) * h - 0.5, 0, h - 1)
        i = numpy.minimum(x.astype(int), w - 2)
        j = numpy.minimum(y.astype(int), h - 2)
        fx = (x - i)[:, None]
        fy = (y - j)[:, None]
        out[at] = (
            rgb[j, i] * (1 - fx) * (1 - fy)
            + rgb[j, i + 1] * fx * (1 - fy)
            + rgb[j + 1, i] * (1 - fx) * fy
            + rgb[j + 1, i + 1] * fx * fy
        )
    return out


################################################################################
def unpacked_copy(dsf, lat, lon, level=1):
    # Copies dsf into tmp and unpacks it when it is a 7z archive, as
    # X-Plane 12 ships them, with notes at verbosity level. Returns the
    # path of the copy, or None after a message.
    copy = os.path.join(FNAMES.Tmp_dir, FNAMES.short_latlon(lat, lon) + ".dsf")
    UI.vprint(level, "-> Making a copy of", dsf, "in tmp dir")
    try:
        shutil.copy(dsf, copy)
    except OSError as e:
        UI.lvprint(0, "   ERROR: could not copy", dsf, "to tmp:", e)
        return None
    with open(copy, "rb") as f:
        packed = f.read(2) == b"7z"
    if packed:
        UI.vprint(level, "-> The DSF is a 7z archive, uncompressing...")
        os.replace(copy, copy + ".7z")
        unpacked = extract_7z(copy + ".7z", FNAMES.Tmp_dir)
        os.remove(copy + ".7z")
        if not unpacked:
            # 7-Zip writes what it extracted even when its CRC fails.
            try:
                os.remove(copy)
            except FileNotFoundError:
                pass
            return None
    return copy


################################################################################
def build_overlay(lat, lon):
    if UI.is_working:
        return 0
    UI.is_working = 1
    timer = time.time()
    UI.logprint("Step 4 for tile lat=", lat, ", lon=", lon, ": starting.")
    UI.vprint(
        0,
        "\nStep 4 : Extracting overlay for tile "
        + FNAMES.short_latlon(lat, lon)
        + " : \n--------\n",
    )
    file_to_sniff = global_scenery_dsf(lat, lon)
    if not os.path.exists(file_to_sniff):
        UI.exit_message_and_bottom_line(
            "   ERROR: file ",
            file_to_sniff,
            "absent. Recall that the overlay source directory needs to be set ",
            "in the config window first.",
        )
        return 0
    file_to_sniff_loc = unpacked_copy(file_to_sniff, lat, lon)
    if not file_to_sniff_loc:
        UI.exit_message_and_bottom_line("")
        return 0
    UI.vprint(1, "-> Converting the copy to text format")
    dsfconvertcmd = [
        dsftool_cmd.strip(),
        " -dsf2text ".strip(),
        file_to_sniff_loc,
        os.path.join(
            FNAMES.Tmp_dir, FNAMES.short_latlon(lat, lon) + "_tmp_dsf.txt"
        ),
    ]
    fingers_crossed = subprocess.Popen(
        dsfconvertcmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    while True:
        line = fingers_crossed.stdout.readline()
        if not line:
            break
        else:
            UI.vprint(1, "     " + line.decode("utf-8", "backslashreplace")[:-1])
    if fingers_crossed.wait():
        UI.exit_message_and_bottom_line("   ERROR: DSFTool crashed.")
        return 0
    UI.vprint(1, "-> Selecting overlays for copy/paste")
    f = open(
        os.path.join(
            FNAMES.Tmp_dir, FNAMES.short_latlon(lat, lon) + "_tmp_dsf.txt"
        ),
        "r",
    )
    g = open(
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.txt",
        ),
        "w",
    )
    line = f.readline()
    g.write("PROPERTY sim/overlay 1\n")
    pol_type = 0
    pol_dict = {}
    exclude_set_updated = False
    full_ovl_exclude_pol = set(ovl_exclude_pol)
    while line:
        if "PROPERTY" in line:
            g.write(line)
        elif "POLYGON_DEF" in line:
            level = 2 if "facade" not in line else 3
            pol_dict[pol_type] = line.split()[1]
            UI.vprint(level, pol_type, ":", pol_dict[pol_type])
            pol_type += 1
            g.write(line)
        elif "NETWORK_DEF" in line:
            g.write(line)
        elif "BEGIN_POLYGON" in line:
            if not exclude_set_updated:
                tmp = set()
                for item in full_ovl_exclude_pol:
                    if isinstance(item, int):
                        tmp.add(item)
                    elif isinstance(item, str):
                        if item and item[0] == "!":
                            item = item[1:]
                            tmp = tmp.union(
                                [k for k in pol_dict if item not in pol_dict[k]]
                            )
                        else:
                            tmp = tmp.union(
                                [k for k in pol_dict if item in pol_dict[k]]
                            )
                full_ovl_exclude_pol = tmp
                exclude_set_updated = True
            pol_type = int(line.split()[1])
            if pol_type not in full_ovl_exclude_pol:
                while line and ("END_POLYGON" not in line):
                    g.write(line)
                    line = f.readline()
                g.write(line)
            else:
                while line and ("END_POLYGON" not in line):
                    line = f.readline()
        elif "BEGIN_SEGMENT" in line:
            road_type = int(line.split()[2])
            if (
                road_type not in ovl_exclude_net
                and "" not in ovl_exclude_net
                and "*" not in ovl_exclude_net
            ):
                while line and ("END_SEGMENT" not in line):
                    g.write(line)
                    line = f.readline()
                g.write(line)
            else:
                while line and ("END_SEGMENT" not in line):
                    line = f.readline()
        line = f.readline()
    f.close()
    g.close()
    UI.vprint(1, "-> Converting back the text DSF to binary format")
    dsfconvertcmd = [
        dsftool_cmd.strip(),
        " -text2dsf ".strip(),
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.txt",
        ),
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.dsf",
        ),
    ]
    fingers_crossed = subprocess.Popen(
        dsfconvertcmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    while True:
        line = fingers_crossed.stdout.readline()
        if not line:
            break
        else:
            print("     " + line.decode("utf-8", "backslashreplace")[:-1])
    if fingers_crossed.wait():
        UI.exit_message_and_bottom_line("   ERROR: DSFTool crashed.")
        return 0
    dest_dir = os.path.join(
        FNAMES.Overlay_dir, "Earth nav data", FNAMES.round_latlon(lat, lon)
    )
    UI.vprint(1, "-> Coping the final overlay DSF in " + dest_dir)
    if not os.path.exists(dest_dir):
        try:
            os.makedirs(dest_dir)
        except:
            UI.exit_message_and_bottom_line(
                "   ERROR: could not create destination directory "
                + str(dest_dir)
            )
            return 0
    shutil.copy(
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.dsf",
        ),
        os.path.join(dest_dir, FNAMES.short_latlon(lat, lon) + ".dsf"),
    )
    os.remove(
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.dsf",
        )
    )
    os.remove(
        os.path.join(
            FNAMES.Tmp_dir,
            FNAMES.short_latlon(lat, lon) + "_tmp_dsf_without_mesh.txt",
        )
    )
    os.remove(
        os.path.join(
            FNAMES.Tmp_dir, FNAMES.short_latlon(lat, lon) + "_tmp_dsf.txt"
        )
    )
    os.remove(file_to_sniff_loc)
    # DSFTool writes each raster of the source DSF beside the text.
    text = os.path.join(
        FNAMES.Tmp_dir, FNAMES.short_latlon(lat, lon) + "_tmp_dsf.txt"
    )
    for raw in glob.glob(glob.escape(text) + ".*.raw"):
        os.remove(raw)
    UI.timings_and_bottom_line(timer)
    return 1
