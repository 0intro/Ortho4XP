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
        "name":"OSX",
        "dir":"mac",
        "suff":"",
        "toolchain":"osxcross-on-linux.cmake"
      }

win_cfg = { 
        "name":"Windows",
        "dir":"win",
        "suff":".exe",
        "toolchain":"mingw-on-linux.cmake"
      }

all_cfg = [lin_cfg, mac_cfg, win_cfg]

def parse_args(argv):
    build_type = ""
    os_cfg = []
    dest = ""
    data_dest = ""
    for arg in argv[1:]:
        if "debug" in arg.lower():
            build_type = "Debug"
        if "release" in arg.lower():
            build_type = "Release"
        if "lin" in arg.lower():
            os_cfg.append(lin_cfg)
        if "mac" in arg.lower():
            os_cfg.append(mac_cfg)
        if "win" in arg.lower():
            os_cfg.append(win_cfg)
            
    if (not  build_type):
        build_type = "Release"
    if (not os_cfg):
        os_cfg = all_cfg
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
        cmd.append("-DCMAKE_BUILD_TYPE=" + build_type)
        run(cmd)
        print("Configuration done for " + cfg["name"] + ".")
