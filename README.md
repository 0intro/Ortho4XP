# Ortho4XP
A scenery generator for the X-Plane flight simulator.

The release archives for Linux and Windows (x86-64) and for macOS (Apple
silicon and Intel) include Python, the modules, the programs, and 7-Zip:
unpack one in a folder you can write to, and run Ortho4XP (Linux and
macOS) or Ortho4XP.cmd (Windows). The macOS archives need macOS 14 or
later on Apple silicon and macOS 15 or later on Intel. On macOS, first
clear the quarantine that the browser puts on the download:

    xattr -dr com.apple.quarantine Ortho4XP

On Linux, the mesh viewer medit needs glibc 2.34 or later (Debian 12,
Ubuntu 22.04, RHEL 9) and the OpenGL and X11 libraries libGL.so.1,
libGLU.so.1, libX11.so.6, libXi.so.6, and libXrandr.so.2, packaged as
libgl1, libglu1-mesa, libx11-6, libxi6, and libxrandr2 on Debian and
Ubuntu. Install_Instructions.txt explains how to run Ortho4XP from
source.

Water is X-Plane 12 water by default (water_tech XP12). Masked water, the sea
and the lakes, takes its colour from the orthophoto, which fades into the
colour of X-Plane's own water away from the shore. Step 3 reads that colour
from the maps of the X-Plane whose Global Scenery custom_overlay_src names.
Textures below mask_zl take their masks from those of Step 2.5 at mask_zl, in
a tile built alone, once Step 2.5 has run with this version. Step 3 computes
the floor of the water and the fetch of the waves from the mesh, as Laminar's
scenery tools do, or takes the floor from a raster that custom_bathymetry
names. The tiles also bring the seasons and the sounds from the corresponding
Global Scenery tiles. The water_tech XP11 + bathy draws the orthophoto as
overlays over X-Plane water instead. A tile or global config keeps the
water_tech that it names. A tile config of 1.30 or before, which names none,
takes that of the global config, and a global config that names none takes
XP12: after a change of water_tech, run Steps 2.5 and 3 again. In XP12 water,
the masks of Step 2.5 hold the lakes as water too, as the mask layers of
combined providers and the alpha of GeoTIFF exports then read them. Masks
drawn before this version, or in XP11 water without use_masks_for_inland, hold
the lakes as land, which keep the photo until Step 2.5 runs again.

The code as been updated to work with recent versions of the python modules it
depends on (you may have experienced some deprecation warnings or even some
code break due to Numpy, Pyproj and Shapely, these should be fixed with this
update).


TODO :
- Check and update providers status.
- Incorporate some code changes that were in the old "devel" version (the
  present is an update from the v130 master branch only).
- Do something about the 3rd party initiatives to provide some Docker or other 
  Plug and Play versions.
