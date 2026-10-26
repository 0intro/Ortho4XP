# Ortho4XP
A scenery generator for the X-Plane flight simulator.

The release archives for Linux and Windows (x86-64) and for macOS (Apple
silicon and Intel) include Python, the modules, the programs, and 7-Zip:
unpack one in a folder you can write to, and run Ortho4XP (Linux and
macOS) or Ortho4XP.cmd (Windows). On macOS, first clear the quarantine
that the browser puts on the download:

    xattr -dr com.apple.quarantine Ortho4XP

On Linux, the mesh viewer medit needs the OpenGL libraries libGL.so.1 and
libGLU.so.1, packaged as libgl1 and libglu1-mesa on Debian and Ubuntu.
Install_Instructions.txt explains how to run Ortho4XP from source.

NOTE 15/02/2024 : In transition to version 1.40.

Version 1.40 is mostly a compatibility update for XP 12 water requirements.
Some newer code related to 3D waterbed rendering is included, but one will have
to wait for XP 12.1 to potentially revise it. The default setting is XP11 (i.e. 
overlay based ) water rendering + bathymetry for 3D water and physics plane interaction. 
The new Ortho4XP tiles also automatically bring the seasons, sounds, etc raster from the
corresponding Global Scenery tiles.

The code as been updated to work with recent versions of the python modules it
depends on (you may have experienced some deprecation warnings or even some
code break due to Numpy, Pyproj and Shapely, these should be fixed with this
update). The only new python module used is skfmm (Fast Marching Method) and is only needed when using 
the distance_masks_too option, the program will run even without it.


TODO :
- Check and update providers status.
- Incorporate some code changes that were in the old "devel" version (the
  present is an update from the v130 master branch only).
- Do something about the 3rd party initiatives to provide some Docker or other 
  Plug and Play versions.
