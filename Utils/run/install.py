#!/usr/bin/env python3

import os
import sys
sys.dont_write_bytecode = True
import shutil

import configure

# Copies the file src into folder through a new file, which it renames
# over the old copy: Linux refuses to write over a program that runs, and
# macOS kills a signed program written over in place when it runs next.
def put(src, folder):
    dst = os.path.join(folder, os.path.basename(src))
    shutil.copy2(src, dst + ".new")
    os.replace(dst + ".new", dst)

def install(src, utils_dir, cfg):
    dst = os.path.join(utils_dir, cfg["dir"])
    os.makedirs(dst, exist_ok = True)
    for name in ("Triangle4XP", "triangle", "moulinette", "DSFTool", "medit"):
        put(os.path.join(src, name + cfg["suff"]), dst)
    put(os.path.join(utils_dir, "src", "DSFTool-LICENSE.txt"), dst)
    put(os.path.join(src, "medit-LICENSE.txt"), dst)
    if cfg["dir"] != "mac":
        put(os.path.join(src, "freeglut-LICENSE.txt"), dst)
    if cfg["dir"] == "win":
        dst = os.path.join(dst, "nvcompress")
        os.makedirs(dst, exist_ok = True)
    put(os.path.join(src, "nvcompress" + cfg["suff"]), dst)
    put(os.path.join(src, "nvtt-LICENSE.txt"), dst)

if __name__ == "__main__":
    (build_type, os_cfg) = configure.parse_args(sys.argv)
    for cfg in os_cfg:
        install(configure.build_dir(build_type, cfg), configure.utils_dir, cfg)
