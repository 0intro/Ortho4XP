#!/usr/bin/env python3
import sys
import os
import threading
import traceback
if not getattr(sys,'frozen',False):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
Ortho4XP_dir='..' if getattr(sys,'frozen',False) else '.'
sys.path.append(os.path.join(Ortho4XP_dir,'src'))

import O4_File_Names as FNAMES
sys.path.append(FNAMES.Provider_dir)
import O4_Imagery_Utils as IMG
import O4_Vector_Map as VMAP
import O4_Mesh_Utils as MESH
import O4_Mask_Utils as MASK
import O4_Tile_Utils as TILE
import O4_Overlay_Utils as OVL
import O4_GUI_Utils as GUI
import O4_UI_Utils as UI
import O4_Config_Utils as CFG  # CFG imported last because it can modify other modules variables


usage="usage: Ortho4XP.py [lat lon [imagery zl]]"

def record_thread_failure(args):
    threading.__excepthook__(args)
    UI.red_flag=True

if __name__ == '__main__':
    if not os.path.isdir(FNAMES.Utils_dir):
        print("Missing ",FNAMES.Utils_dir,"directory, check your install. Exiting.",file=sys.stderr)
        sys.exit(1)
    for directory in (FNAMES.Preview_dir, FNAMES.Provider_dir, FNAMES.Extent_dir, FNAMES.Filter_dir, FNAMES.OSM_dir,
                      FNAMES.Mask_dir,FNAMES.Imagery_dir,FNAMES.Elevation_dir,FNAMES.Geotiff_dir,FNAMES.Patch_dir,
                      FNAMES.Tile_dir,FNAMES.Tmp_dir):
        if not os.path.isdir(directory):
            try: 
                os.makedirs(directory)
                print("Creating missing directory",directory)
            except: 
                print("Could not create required directory",directory,". Exit.",file=sys.stderr)
                sys.exit(1)
    if sys.platform=='win32':
        hint="copy Utils\\win from the Windows release archive"
    else:
        hint="build the programs with Utils/run/configure.py, build.py, and install.py"
    for program in (MESH.Triangle4XP_cmd,MESH.triangle_cmd,MESH.sort_mesh_cmd,IMG.dds_convert_cmd,OVL.dsftool_cmd):
        if not os.path.isfile(program.strip()):
            print("Missing",program.strip()+":",hint+", as Install_Instructions.txt explains.",file=sys.stderr)
    IMG.initialize_extents_dict()
    IMG.initialize_color_filters_dict()
    IMG.initialize_providers_dict()
    IMG.initialize_combined_providers_dict()
    if len(sys.argv)==1: # switch to the graphical interface
        Ortho4XP = GUI.Ortho4XP_GUI()

        Ortho4XP.mainloop()	    
        print("Bon vol!")
    else: # sequel is only concerned with command line 
        if len(sys.argv) not in (3,5):
            print(usage,file=sys.stderr); sys.exit(2)
        try:
            lat=int(sys.argv[1])
            lon=int(sys.argv[2])
            zoomlevel=int(sys.argv[4]) if len(sys.argv)==5 else 0
        except ValueError:
            print(usage,file=sys.stderr); sys.exit(2)
        tile=CFG.Tile(lat,lon,'')
        if len(sys.argv)==3:
            if not tile.read_from_config():
                sys.exit(1)
        else:
            tile.default_website=sys.argv[3]
            tile.default_zl=zoomlevel
        threading.excepthook=record_thread_failure
        for step in (VMAP.build_poly_file,MESH.build_mesh,MASK.build_masks,TILE.build_tile):
            try:
                done=step(tile)
            except Exception:
                traceback.print_exc()
                done=0
            if not done or UI.red_flag:
                sys.exit(1)
        print("Bon vol!")
