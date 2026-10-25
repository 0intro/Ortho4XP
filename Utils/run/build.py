#!/usr/bin/env python3

import sys
sys.dont_write_bytecode = True

import configure

if __name__ == "__main__":
    (build_type, os_cfg) = configure.parse_args(sys.argv)
    for cfg in os_cfg:
        print()
        print("--------------------------------------------------------")
        print("Building Ortho4XP C executables in {:s} mode for {:s}.".format(build_type, cfg["name"]))
        print("--------------------------------------------------------")
        configure.run(["cmake", "--build", configure.build_dir(build_type, cfg)])
        print("Build done for " + cfg["name"] + ".")
