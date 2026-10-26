# Patch X-Plane Scenery Tools 23-4 for libc++ and clang. Runs in the source
# directory.
#
# libc++'s <fstream> calls std::fopen, which the fopen macro of XDefs.h turns
# into std::x_fopen. Declare x_fopen in std too, as xptools master does.
#
# The point pool writers pass their output vector as &*v.begin(), which is
# undefined for an empty vector. The curved vector pool is always empty.
# From that dereference, clang concludes that the pool holds points, and the
# x86-64 macOS build reads its first point through a null pointer. Use
# v.data() instead.
#
# DSFTool prints the date it was compiled on, which would make the same
# sources build different programs. Print the date of the xptools commit.

cmake_minimum_required(VERSION 3.21)
include(${CMAKE_CURRENT_LIST_DIR}/patch.cmake)

set(old "\t#define fopen(_Filename,_Mode) x_fopen(_Filename, _Mode)")
patch(src/Obj/XDefs.h "${old}"
    "\t#ifdef __cplusplus\n\t\tnamespace std { using ::x_fopen; }\n\t#endif\n${old}")
patch(src/DSF/DSFPointPool.cpp "(int16_t *) &*shorts.begin()"
    "(int16_t *) shorts.data()")
patch(src/DSF/DSFPointPool.cpp "(int *) &*longs.begin()"
    "(int *) longs.data()")
patch(src/XPTools/version.h "__DATE__\".\\n\"" "\"May 30 2023.\\n\"")
