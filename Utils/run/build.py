#!/usr/bin/env python3

import os
import subprocess
import sys
sys.dont_write_bytecode = True

import configure

# The commit time pins the __DATE__ that DSFTool prints, so that a commit
# always builds the same programs.
def set_source_date():
    if "SOURCE_DATE_EPOCH" in os.environ:
        return
    try:
        r = subprocess.run(["git", "-C", configure.utils_dir, "log", "-1", "--format=%ct"],
            stdout = subprocess.PIPE, stderr = subprocess.DEVNULL, text = True)
    except OSError:
        return
    if r.returncode == 0:
        os.environ["SOURCE_DATE_EPOCH"] = r.stdout.strip()

if __name__ == "__main__":
    set_source_date()
    (build_type, os_cfg) = configure.parse_args(sys.argv)
    for cfg in os_cfg:
        print()
        print("--------------------------------------------------------")
        print("Building Ortho4XP C executables in {:s} mode for {:s}.".format(build_type, cfg["name"]))
        print("--------------------------------------------------------")
        configure.run(["cmake", "--build", configure.build_dir(build_type, cfg)])
        print("Build done for " + cfg["name"] + ".")
