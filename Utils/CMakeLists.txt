cmake_minimum_required(VERSION 3.14...3.24)
cmake_policy(VERSION 3.14...3.24)

project(Ortho4XP VERSION 1.4 LANGUAGES C CXX)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_VERBOSE_MAKEFILE OFF)
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE OFF)

# GCC 16 lists gcc_s_asneeded among the libraries that C links on its
# own. CMake adds it to links that mix C and C++, and it exists only as
# a shared library, which a static link refuses. g++ needs none of it.
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES gcc_s_asneeded)

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

include(FetchContent)

# DSFTool from X-Plane Scenery Tools 23-4, the head of its release branch.
FetchContent_Declare(xptools
    URL https://github.com/X-Plane/xptools/archive/4610cac6a0ee8692840740ea93a9ba920330166b.tar.gz
    URL_HASH SHA256=d55f532a5f8e2e39561636d2a09edbd2d4ad7939fc8668a1d4c3dda4dd66d3bd
    PATCH_COMMAND ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_SOURCE_DIR}/src/patch-dsftool.cmake
)
FetchContent_MakeAvailable(xptools)
set(xpt ${xptools_SOURCE_DIR}/src)
set(lzma 7zAlloc 7zArcIn 7zBuf 7zCrc 7zCrcOpt 7zDec 7zFile 7zStream Bcj2 Bra
    Bra86 BraIA64 CpuArch Delta Lzma2Dec LzmaDec)
list(TRANSFORM lzma PREPEND ${xpt}/lzma19/C/)
list(TRANSFORM lzma APPEND .c)
add_executable(DSFTool
    ${xpt}/DSFTools/DSFToolCmdLine.cpp ${xpt}/DSFTools/DSF2Text.cpp
    ${xpt}/DSF/DSFLib.cpp ${xpt}/DSF/DSFLibWrite.cpp ${xpt}/DSF/DSFPointPool.cpp
    ${xpt}/DSF/tri_stripper_101/tri_stripper.cpp
    ${xpt}/Utils/AssertUtils.cpp ${xpt}/Utils/FileUtils.cpp
    ${xpt}/Utils/XChunkyFileUtils.cpp ${xpt}/Utils/md5.c ${lzma})
if(WIN32)
    target_sources(DSFTool PRIVATE ${xpt}/GUI/GUI_Unicode.cpp)
    target_compile_definitions(DSFTool PRIVATE IBM=1 APL=0 LIN=0)
elseif(APPLE)
    target_compile_definitions(DSFTool PRIVATE IBM=0 APL=1 LIN=0)
else()
    target_compile_definitions(DSFTool PRIVATE IBM=0 APL=0 LIN=1)
endif()
target_compile_definitions(DSFTool PRIVATE DEV=0)
target_include_directories(DSFTool PRIVATE ${xpt}/GUI ${xpt}/Utils ${xpt}/DSF
    ${xpt}/DSF/tri_stripper_101 ${xpt}/lzma19/C)
# The DSF atom names are multicharacter constants.
target_compile_options(DSFTool PRIVATE "SHELL:-include ${xpt}/Obj/XDefs.h"
    -Wno-multichar)
# C++17 and later make the byte of MinGW's rpcndr.h clash with std::byte.
set_target_properties(DSFTool PROPERTIES
    CXX_STANDARD 14 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)

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
