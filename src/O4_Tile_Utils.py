import os
import re
import time
import shutil
import queue
import threading
import O4_UI_Utils as UI
import O4_File_Names as FNAMES
import O4_Imagery_Utils as IMG
import O4_Vector_Map as VMAP
import O4_Mesh_Utils as MESH
import O4_Mask_Utils as MASK
import O4_DSF_Utils as DSF
import O4_Overlay_Utils as OVL
from O4_Parallel_Utils import parallel_launch, parallel_join

max_convert_slots = 4
skip_downloads = False
skip_converts = False

################################################################################
def download_textures(tile, download_queue, convert_queue, failed):
    UI.vprint(1, "-> Opening download queue.")
    done = 0
    while True:
        texture_attributes = download_queue.get()
        if isinstance(texture_attributes, str) and texture_attributes == "quit":
            UI.progress_bar(2, 100)
            break
        # A texture whose build raises fails too, and the exception goes
        # on to threading.excepthook.
        built = 0
        try:
            built = IMG.build_jpeg_ortho(tile, *texture_attributes)
        finally:
            if not built:
                failed.append(texture_attributes)
        if built:
            done += 1
            UI.progress_bar(
                2, int(100 * done / (done + download_queue.qsize()))
            )
            convert_queue.put((tile, *texture_attributes))
        if UI.red_flag:
            UI.vprint(1, "Download process interrupted.")
            return 0
    if done:
        UI.vprint(1, " *Download of textures completed.")
    return 1

################################################################################
def build_tile(tile, keep_mesh=False):
    if UI.is_working:
        return 0
    UI.is_working = 1
    UI.red_flag = False
    UI.logprint(
        "Step 3 for tile lat=", tile.lat, ", lon=", tile.lon, ": starting."
    )
    UI.vprint(
        0,
        "\nStep 3 : Building DSF/Imagery for tile "
        + FNAMES.short_latlon(tile.lat, tile.lon)
        + " : \n--------\n",
    )

    if not os.path.isfile(FNAMES.mesh_file(tile.build_dir, tile.lat, tile.lon)):
        UI.lvprint(
            0, "ERROR: A mesh file must first be constructed for the tile!"
        )
        UI.exit_message_and_bottom_line("")
        return 0

    timer = time.time()

    tile.write_to_config()

    if not IMG.initialize_local_combined_providers_dict(tile):
        UI.exit_message_and_bottom_line("")
        return 0

    try:
        if not os.path.exists(
            os.path.join(
                tile.build_dir,
                "Earth nav data",
                FNAMES.round_latlon(tile.lat, tile.lon),
            )
        ):
            os.makedirs(
                os.path.join(
                    tile.build_dir,
                    "Earth nav data",
                    FNAMES.round_latlon(tile.lat, tile.lon),
                )
            )
        if not os.path.isdir(os.path.join(tile.build_dir, "textures")):
            os.makedirs(os.path.join(tile.build_dir, "textures"))
        remove_texture_masks(tile)
        if not tile.grouped:
            try:
                shutil.rmtree(os.path.join(tile.build_dir, "terrain"))
            except:
                pass
        if not os.path.isdir(os.path.join(tile.build_dir, "terrain")):
            os.makedirs(os.path.join(tile.build_dir, "terrain"))
    except Exception as e:
        UI.lvprint(0, "ERROR: Cannot create tile subdirectories.")
        UI.vprint(3, e)
        UI.exit_message_and_bottom_line("")
        return 0

    if skip_downloads and (
        tile.imprint_masks_to_dds or tile.water_tech == "XP12"
    ):
        UI.vprint(
            1,
            "-> Downloads are skipped, so the masks of the water go to PNG",
            "files beside the textures.",
        )
    OVL.water_colour_maps.clear()
    maps = OVL.water_colour_dir()
    if tile.water_color_mask and not (maps and os.path.isdir(maps)):
        UI.lvprint(
            1,
            "   WARNING: the maps of the colour of X-Plane's water are",
            "missing" + (" in " + maps if maps else ""),
            "(Resources/bitmaps/world/water of the X-Plane whose Global",
            "Scenery custom_overlay_src names), so the masked water fades",
            "into the colour of the open sea of any.png.",
        )
    download_queue = queue.Queue()
    convert_queue = queue.Queue()
    
    download_launched = False
    convert_launched = False
    failed_downloads = []
    converted = 1

    built = []
    build_dsf_thread = threading.Thread(
        target=lambda: built.append(DSF.build_dsf(tile, download_queue))
    )
    download_thread = threading.Thread(
        target=download_textures,
        args=[tile, download_queue, convert_queue, failed_downloads],
    )
    build_dsf_thread.start()
    if not skip_downloads:
        download_thread.start()
        download_launched = True
        if not skip_converts:
            UI.vprint(
                1,
                "-> Opening convert queue and",
                max_convert_slots,
                "conversion workers.",
            )
            dico_conv_progress = {"done": 0, "bar": 3}
            convert_workers = parallel_launch(
                IMG.convert_texture,
                convert_queue,
                max_convert_slots,
                progress=dico_conv_progress,
            )
            convert_launched = True
    build_dsf_thread.join()
    if download_launched:
        download_queue.put("quit")
        download_thread.join()
        if convert_launched:
            for _ in range(max_convert_slots):
                convert_queue.put("quit")
            converted = parallel_join(convert_workers)
            if UI.red_flag:
                UI.vprint(1, "DDS conversion process interrupted.")
            elif dico_conv_progress["done"] >= 1:
                UI.vprint(1, " *DDS conversion of textures completed.")
    # build_dsf writes the DSF to a .tmp file, which becomes the DSF of the
    # tile only when it succeeds. One that stopped, failed, or raised
    # stays inactive. The DSF of an earlier run then goes to .bak in a
    # tile built alone, since the terrain files that it names went as
    # Step 3 started.
    dsf_file_name = os.path.join(
        tile.build_dir,
        "Earth nav data",
        FNAMES.long_latlon(tile.lat, tile.lon) + ".dsf",
    )
    activated = False
    if built == [1] and not UI.red_flag:
        UI.vprint(1, " *Activating DSF file.")
        try:
            if os.path.exists(dsf_file_name):
                os.replace(dsf_file_name, dsf_file_name + ".bak")
            os.replace(dsf_file_name + ".tmp", dsf_file_name)
            activated = True
        except:
            UI.vprint(
                0, "ERROR : could not rename DSF file, tile is not actived."
            )
    elif not tile.grouped and os.path.exists(dsf_file_name):
        try:
            os.replace(dsf_file_name, dsf_file_name + ".bak")
        except OSError as e:
            UI.vprint(0, "ERROR: could not move", dsf_file_name, "to .bak:", e)
    if UI.red_flag:
        UI.exit_message_and_bottom_line()
        return 0
    if not activated or failed_downloads or not converted:
        UI.exit_message_and_bottom_line(
            "\nERROR: Some textures or the DSF could not be built, see above."
        )
        return 0
    if UI.cleaning_level > 1:
        try:
            os.remove(FNAMES.alt_file(tile))
        except:
            pass
        try:
            os.remove(FNAMES.input_node_file(tile))
        except:
            pass
        try:
            os.remove(FNAMES.input_poly_file(tile))
        except:
            pass
    if UI.cleaning_level > 2:
        if not keep_mesh:
            try:
                os.remove(FNAMES.mesh_file(tile.build_dir, tile.lat, tile.lon))
            except:
                pass
        for apt_file in (FNAMES.apt_file(tile), FNAMES.legacy_apt_file(tile)):
            try:
                os.remove(apt_file)
            except:
                pass
    if UI.cleaning_level > 1 and not tile.grouped:
        remove_unwanted_textures(tile)
    UI.timings_and_bottom_line(timer)
    UI.logprint(
        "Step 3 for tile lat=", tile.lat, ", lon=", tile.lon, ": normal exit."
    )
    return 1

################################################################################
def remove_texture_masks(tile):
    # The PNG files beside the textures of a tile built alone, which Step 3
    # writes again: from cleaning level 2 all of them, the masks and
    # water_transition.png, and at any level the masks named before each
    # texture took the name of its provider too, which nothing reads.
    if tile.grouped:
        return
    folder = os.path.join(tile.build_dir, "textures")
    for f in os.listdir(folder):
        old = re.fullmatch(r"\d+_\d+_ZL\d+\.png", f)
        if f[-4:] == ".png" and (UI.cleaning_level > 1 or old):
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


################################################################################
def build_all(tile):
    for step in (
        VMAP.build_poly_file,
        MESH.build_mesh,
        MASK.build_masks,
        build_tile,
    ):
        if not step(tile) or UI.red_flag:
            UI.exit_message_and_bottom_line("")
            return 0
    UI.is_working = 0
    return 1

################################################################################
def build_tile_list(
    tile, list_lat_lon, do_osm, do_mesh, do_mask, do_dsf, do_ovl, do_ptc
):
    if UI.is_working:
        return 0
    UI.red_flag = 0
    timer = time.time()
    UI.lvprint(
        0, "Batch build launched for a number of", len(list_lat_lon), "tiles."
    )

    def stopped(lat, lon):
        if UI.red_flag:
            UI.exit_message_and_bottom_line()
        else:
            UI.exit_message_and_bottom_line(
                "\nERROR: Batch build stopped at tile "
                + FNAMES.short_latlon(lat, lon)
                + "."
            )
        return 0

    def done(lat, lon):
        try:
            UI.gui.earth_window.canvas.delete(
                UI.gui.earth_window.dico_tiles_todo[(lat, lon)]
            )
            UI.gui.earth_window.dico_tiles_todo.pop((lat, lon), None)
        except:
            pass

    # Each tile reads its config over the settings that the batch began
    # with, so that a setting of one tile's config stays with that tile.
    # The later steps of a tile take the settings that its Steps 1 and 2
    # left, as Step 1 sets iterate to 0.
    settings = tile.settings()
    meshed = {}

    def start(k, lat, lon):
        UI.vprint(
            1,
            "Dealing with tile ",
            k,
            "/",
            len(list_lat_lon),
            ":",
            FNAMES.short_latlon(lat, lon),
        )
        (tile.lat, tile.lon) = (lat, lon)
        tile.build_dir = FNAMES.build_dir(
            tile.lat, tile.lon, tile.custom_build_dir
        )
        tile.dem = None
        if (lat, lon) in meshed:
            tile.restore_settings(meshed[(lat, lon)])
        elif do_ptc:
            tile.restore_settings(settings)
            tile.read_from_config()

    # Steps 1 and 2 of every tile come first, so that Steps 2.5 and 3 of
    # each find the meshes of the tiles around it, whose water they take
    # at its edges. The meshes stay until the batch ends, and after a stop
    # for its rerun.
    later = do_mask or do_dsf or do_ovl
    if do_osm or do_mesh:
        for (k, (lat, lon)) in enumerate(list_lat_lon, 1):
            start(k, lat, lon)
            tile.make_dirs()
            if do_osm and (not VMAP.build_poly_file(tile) or UI.red_flag):
                return stopped(lat, lon)
            if do_mesh and (not MESH.build_mesh(tile) or UI.red_flag):
                return stopped(lat, lon)
            if later:
                meshed[(lat, lon)] = tile.settings()
            else:
                done(lat, lon)
    for (k, (lat, lon)) in enumerate(list_lat_lon, 1):
        if later:
            start(k, lat, lon)
        if do_dsf:
            tile.make_dirs()
        if do_mask and (not MASK.build_masks(tile) or UI.red_flag):
            return stopped(lat, lon)
        if do_dsf and (not build_tile(tile, keep_mesh=True) or UI.red_flag):
            return stopped(lat, lon)
        if do_ovl and (not OVL.build_overlay(lat, lon) or UI.red_flag):
            return stopped(lat, lon)
        done(lat, lon)
    if do_dsf and UI.cleaning_level > 2:
        for (lat, lon) in list_lat_lon:
            build_dir = FNAMES.build_dir(lat, lon, tile.custom_build_dir)
            try:
                os.remove(FNAMES.mesh_file(build_dir, lat, lon))
            except:
                pass
    UI.lvprint(
        0, "Batch process completed in", UI.nicer_timer(time.time() - timer)
    )
    return 1

################################################################################
def remove_unwanted_textures(tile):
    texture_list = []
    for f in os.listdir(os.path.join(tile.build_dir, "terrain")):
        if f[-4:] != ".ter":
            continue
        name = f[:-4]
        for suffix in ("_overlay", "_sea", "_water"):
            name = name.removesuffix(suffix)
        texture_list.append(name + ".dds")
    for f in os.listdir(os.path.join(tile.build_dir, "textures")):
        if f[-4:] != ".dds":
            continue
        if f not in texture_list:
            print("Removing obsolete texture", f)
            try:
                os.remove(os.path.join(tile.build_dir, "textures", f))
            except:
                pass
