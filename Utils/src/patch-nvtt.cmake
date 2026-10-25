# Patch NVIDIA Texture Tools 2.1.2 to build nvcompress for any x86-64
# CPU, natively and with MinGW-w64. Runs in the NVTT source directory.

function(replace path old new)
    file(READ ${path} text)
    string(REPLACE "${old}" "${new}" patched "${text}")
    if(patched STREQUAL text)
        message(FATAL_ERROR "${path}: missing ${old}")
    endif()
    file(WRITE ${path} "${patched}")
endfunction()

# OptimalOptions.cmake adds -march=native.
replace(CMakeLists.txt "INCLUDE(\${NV_CMAKE_DIR}/OptimalOptions.cmake)" "")

# The Windows code assumes MSVC: the _nolock stdio functions, structured
# exception handling, the __cpuid intrinsic, and alloca in malloc.h.
replace(src/nvcore/StdStream.h "#if NV_OS_WIN32" "#if NV_CC_MSVC")
replace(src/nvcore/Debug.cpp
    "#if NV_OS_WIN32\n    EnterCriticalSection"
    "#if NV_OS_WIN32 && NV_CC_MSVC\n    EnterCriticalSection")
replace(src/nvcore/Debug.cpp
    "#if NV_OS_WIN32\n#if NV_USE_SEPARATE_THREAD"
    "#if NV_OS_WIN32 && NV_CC_MSVC\n#if NV_USE_SEPARATE_THREAD")
replace(src/nvthread/Thread.cpp "    __try\n" "#if NV_CC_MSVC\n    __try\n")
replace(src/nvthread/Thread.cpp
    "    __except(EXCEPTION_EXECUTE_HANDLER)\n    {\n    }\n"
    "    __except(EXCEPTION_EXECUTE_HANDLER)\n    {\n    }\n#endif\n")
replace(src/nvthread/nvthread.cpp
    "#include \"Win32.h\""
    "#include \"Win32.h\"\n#include <intrin.h>")
replace(src/nvmath/Matrix.cpp
    "#if !NV_CC_MSVC && !NV_OS_ORBIS"
    "#if NV_OS_WIN32\n#include <malloc.h>\n#elif !NV_OS_ORBIS")

# GCC needs inline beside always_inline, as in DefsGnucLinux.h.
replace(src/nvcore/DefsGnucWin32.h
    "#define NV_FORCEINLINE\t__attribute__"
    "#define NV_FORCEINLINE\tinline __attribute__")
