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

NOTE 15/02/2024 : In transition to version 1.40.

Version 1.40 is mostly a compatibility update for XP 12 water requirements.

Water is X-Plane 12 water by default (water_tech XP12). Masked water, the
sea and the lakes in textures at mask_zl or above, takes its colour from the
orthophoto, which fades into the colour of X-Plane's own water away from the
shore. Step 3 reads that colour from the maps of the X-Plane whose Global
Scenery custom_overlay_src names. It computes the floor of the water and the
fetch of the waves from the mesh, as Laminar's scenery tools do, or takes the
floor from a raster that custom_bathymetry names. The tiles also bring the
seasons and the sounds from the corresponding Global Scenery tiles. The
water_tech XP11 + bathy draws the orthophoto as overlays over X-Plane water
instead. A tile or global config keeps the water_tech it was written with: after
changing it, run Steps 2.5 and 3 again. In XP12 water, the masks of Step 2.5
hold the lakes as water too, as the mask layers of combined providers and the
alpha of GeoTIFF exports then read them.

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
