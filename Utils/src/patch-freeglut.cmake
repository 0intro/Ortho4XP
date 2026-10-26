# Patch freeglut 3.8.0: without a window manager that follows the EWMH,
# it has no _NET_WM_NAME and _NET_WM_ICON_NAME atoms, and setting a
# title deletes the property None, an X error that ends the program.
# Runs in the freeglut source directory.

cmake_minimum_required(VERSION 3.21)
include(${CMAKE_CURRENT_LIST_DIR}/patch.cmake)

foreach(atom NetWMName NetWMIconName)
    patch(src/x11/fg_window_x11.c
        "if(set_utf8_title(win, fgDisplay.pDisplay.${atom}, str) == -1) {"
        "if(fgDisplay.pDisplay.${atom} && set_utf8_title(win, fgDisplay.pDisplay.${atom}, str) == -1) {")
endforeach()
