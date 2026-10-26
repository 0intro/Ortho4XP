#!/usr/bin/env python3

import os
import subprocess
import sys
sys.dont_write_bytecode = True

utils_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

lin_cfg = { 
        "name":"Linux",
        "dir":"lin",
        "suff" : "",
        "toolchain":""
      }

mac_cfg = { 
        "name":"macOS",
        "dir":"mac",
        "suff":"",
        "toolchain":""
      }

win_cfg = { 
        "name":"Windows",
        "dir":"win",
        "suff":".exe",
        "toolchain":"mingw-on-linux.cmake"
      }

systems = {"lin": lin_cfg, "mac": mac_cfg, "win": win_cfg}

# The build type and the systems that the arguments name, release and the
# systems that this host builds by default. configure.py, build.py, and
# install.py take the same arguments.
def parse_args(argv):
    build_type = "Release"
    os_cfg = []
    for arg in argv[1:]:
        word = arg.lower()
        if word in ("debug", "release"):
            build_type = word.capitalize()
        elif word in systems:
            if systems[word] not in os_cfg:
                os_cfg.append(systems[word])
        else:
            print("usage: " + os.path.basename(argv[0]) + " [debug|release] [lin] [mac] [win]", file = sys.stderr)
            sys.exit(2)
    if not os_cfg:
        os_cfg = [mac_cfg] if sys.platform == "darwin" else [lin_cfg, win_cfg]
    return (build_type, os_cfg)

def build_dir(build_type, cfg):
    return os.path.join(utils_dir, "build", build_type.lower(), cfg["dir"])

def run(cmd):
    print("Executing : " + " ".join(cmd))
    status = subprocess.call(cmd)
    if status:
        sys.exit(cmd[0] + " exited with status " + str(status))

   
if __name__ == "__main__":
    (build_type, os_cfg) = parse_args(sys.argv)
    for cfg in os_cfg:
        print()
        print("--------------------------------------------------------")
        print("Configurating Ortho4XP C executables {:s} mode for {:s}.".format(build_type, cfg["name"]))
        print("--------------------------------------------------------")
        cmd = ["cmake", "-S", utils_dir, "-B", build_dir(build_type, cfg)]
        if cfg["toolchain"]:
            cmd.append("-DCMAKE_TOOLCHAIN_FILE=" + os.path.join(utils_dir, "toolchains", cfg["toolchain"]))
        # The install step of NVTT copies nvcompress from where a generator
        # of a single configuration puts it, whatever CMAKE_GENERATOR says.
        cmd += ["-G", "Unix Makefiles"]
        if cfg == mac_cfg:
            # Universal programs, for macOS 14 and later, as the wheels of
            # the archives need.
            if sys.platform != "darwin":
                sys.exit("configure.py: the macOS programs build on a Mac")
            cmd += ["-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0"]
        cmd.append("-DCMAKE_BUILD_TYPE=" + build_type)
        run(cmd)
        print("Configuration done for " + cfg["name"] + ".")
