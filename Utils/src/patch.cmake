# patch(path old new) replaces the text old in the file path with new, and
# stops the build when old is missing. A patch step runs again after its
# script changes or after a failure, so a patch script starts from the
# files as fetched: path.o4orig keeps each file that a run patched, and
# including this file puts every one of them back.

file(GLOB_RECURSE saved_files "*.o4orig")
foreach(saved ${saved_files})
    string(REGEX REPLACE "\\.o4orig$" "" path "${saved}")
    file(COPY_FILE "${saved}" "${path}")
endforeach()

function(patch path old new)
    if(NOT EXISTS ${path}.o4orig)
        file(COPY_FILE ${path} ${path}.o4orig)
    endif()
    file(READ ${path} text)
    string(REPLACE "${old}" "${new}" patched "${text}")
    if(patched STREQUAL text)
        message(FATAL_ERROR "${CMAKE_CURRENT_SOURCE_DIR}/${path}: missing ${old}\n"
            "If an earlier build patched the file, delete the build folder "
            "that holds it and configure again.")
    endif()
    file(WRITE ${path} "${patched}")
endfunction()
