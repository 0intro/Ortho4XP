cmake_minimum_required(VERSION 3.13...3.24)
cmake_policy(VERSION 3.13...3.24)

project(Ortho4XP VERSION 1.4 LANGUAGES C)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_VERBOSE_MAKEFILE OFF)
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE OFF)

# Static programs run on any Linux distribution and any Windows.
# macOS supports only dynamic linking.
if(NOT APPLE)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
endif()

add_executable(Triangle4XP src/Triangle4XP.c)
target_link_libraries(Triangle4XP m)

add_executable(triangle src/triangle.c)
target_compile_definitions(triangle PRIVATE ANSI_DECLARATORS)
target_link_libraries(triangle m)

add_executable(moulinette src/moulinette.c)
target_link_libraries(moulinette m)

include(ExternalProject)

ExternalProject_Add(nvtt
    URL https://github.com/castano/nvidia-texture-tools/archive/refs/tags/2.1.2.tar.gz
    URL_HASH SHA256=0187336b0285038fab4f4a6b7654f51beaebab040b6aad53c147c917c5ab519b
    PATCH_COMMAND ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_SOURCE_DIR}/src/patch-nvtt.cmake
    CMAKE_ARGS
        -DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}
        -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_EXE_LINKER_FLAGS=${CMAKE_EXE_LINKER_FLAGS}
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
        -DCMAKE_DISABLE_FIND_PACKAGE_CUDA=ON
    BUILD_COMMAND ${CMAKE_COMMAND} --build <BINARY_DIR> --target nvcompress
    INSTALL_COMMAND ${CMAKE_COMMAND} -E copy
        <BINARY_DIR>/src/nvtt/tools/nvcompress${CMAKE_EXECUTABLE_SUFFIX}
        ${CMAKE_CURRENT_BINARY_DIR}
    COMMAND ${CMAKE_COMMAND} -E copy
        <SOURCE_DIR>/LICENSE ${CMAKE_CURRENT_BINARY_DIR}/nvtt-LICENSE.txt
)
