# patch(path old new) replaces the text old in the file path with new, and
# stops the build when old is missing. A patch step runs again after its
# script changes or after a failure, so the first patch of a file in a run
# starts from the file as fetched, which path.orig keeps.

function(patch path old new)
    get_property(started GLOBAL PROPERTY patched_files)
    if(NOT "${path}" IN_LIST started)
        if(EXISTS ${path}.orig)
            file(COPY_FILE ${path}.orig ${path})
        else()
            file(COPY_FILE ${path} ${path}.orig)
        endif()
        set_property(GLOBAL APPEND PROPERTY patched_files ${path})
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
