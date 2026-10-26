# Patch freeglut 3.8.0: without a window manager that follows the EWMH,
# it has no _NET_WM_NAME and _NET_WM_ICON_NAME atoms, and setting a
# title deletes the property None, an X error that ends the program.
# Runs in the freeglut source directory.

foreach(atom NetWMName NetWMIconName)
    set(path src/x11/fg_window_x11.c)
    set(old "if(set_utf8_title(win, fgDisplay.pDisplay.${atom}, str) == -1) {")
    set(new "if(fgDisplay.pDisplay.${atom} && set_utf8_title(win, fgDisplay.pDisplay.${atom}, str) == -1) {")
    file(READ ${path} text)
    string(REPLACE "${old}" "${new}" patched "${text}")
    if(patched STREQUAL text)
        message(FATAL_ERROR "${path}: missing ${old}")
    endif()
    file(WRITE ${path} "${patched}")
endforeach()
