# Patch NVIDIA Texture Tools 2.1.2 to build nvcompress for any x86-64
# CPU, natively and with MinGW-w64, and for arm64 Macs. Runs in the NVTT
# source directory.

cmake_minimum_required(VERSION 3.21)
include(${CMAKE_CURRENT_LIST_DIR}/patch.cmake)

# OptimalOptions.cmake adds -march=native.
patch(CMakeLists.txt "INCLUDE(\${NV_CMAKE_DIR}/OptimalOptions.cmake)" "")

# The Windows code assumes MSVC: the _nolock stdio functions, structured
# exception handling, the __cpuid intrinsic, and alloca in malloc.h.
patch(src/nvcore/StdStream.h "#if NV_OS_WIN32" "#if NV_CC_MSVC")
patch(src/nvcore/Debug.cpp
    "#if NV_OS_WIN32\n    EnterCriticalSection"
    "#if NV_OS_WIN32 && NV_CC_MSVC\n    EnterCriticalSection")
patch(src/nvcore/Debug.cpp
    "#if NV_OS_WIN32\n#if NV_USE_SEPARATE_THREAD"
    "#if NV_OS_WIN32 && NV_CC_MSVC\n#if NV_USE_SEPARATE_THREAD")
patch(src/nvthread/Thread.cpp "    __try\n" "#if NV_CC_MSVC\n    __try\n")
patch(src/nvthread/Thread.cpp
    "    __except(EXCEPTION_EXECUTE_HANDLER)\n    {\n    }\n"
    "    __except(EXCEPTION_EXECUTE_HANDLER)\n    {\n    }\n#endif\n")
patch(src/nvthread/nvthread.cpp
    "#include \"Win32.h\""
    "#include \"Win32.h\"\n#include <intrin.h>")
patch(src/nvmath/Matrix.cpp
    "#if !NV_CC_MSVC && !NV_OS_ORBIS"
    "#if NV_OS_WIN32\n#include <malloc.h>\n#elif !NV_OS_ORBIS")

# nvcore.h names arm64 NV_CPU_ARM_64, and Debug.h and Debug.cpp test
# NV_CPU_AARCH64, as castano/nvidia-texture-tools#330 fixed after 2.1.2.
patch(src/nvcore/nvcore.h "#   define NV_CPU_ARM_64 1" "#   define NV_CPU_AARCH64 1")
patch(src/nvcore/nvcore.h "// NV_CPU_ARM_64" "// NV_CPU_AARCH64")
patch(src/nvcore/Debug.cpp
    "#    elif NV_CPU_ARM\n        ucontext_t"
    "#    elif NV_CPU_ARM || NV_CPU_AARCH64\n        ucontext_t")

# GCC needs inline beside always_inline, as in DefsGnucLinux.h.
patch(src/nvcore/DefsGnucWin32.h
    "#define NV_FORCEINLINE\t__attribute__"
    "#define NV_FORCEINLINE\tinline __attribute__")
