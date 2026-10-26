#!/usr/bin/env python3

import os
import sys
sys.dont_write_bytecode = True
import shutil

import configure

def install(src, utils_dir, cfg):
    dst = os.path.join(utils_dir, cfg["dir"])
    os.makedirs(dst, exist_ok = True)
    for name in ("Triangle4XP", "triangle", "moulinette", "DSFTool", "medit"):
        shutil.copy2(os.path.join(src, name + cfg["suff"]), dst)
    shutil.copy2(os.path.join(utils_dir, "src", "DSFTool-LICENSE.txt"), dst)
    shutil.copy2(os.path.join(src, "medit-LICENSE.txt"), dst)
    if cfg["dir"] != "mac":
        shutil.copy2(os.path.join(src, "freeglut-LICENSE.txt"), dst)
    if cfg["dir"] == "win":
        dst = os.path.join(dst, "nvcompress")
    shutil.copy2(os.path.join(src, "nvcompress" + cfg["suff"]), dst)
    shutil.copy2(os.path.join(src, "nvtt-LICENSE.txt"), dst)

if __name__ == "__main__":
    (build_type, os_cfg) = configure.parse_args(sys.argv)
    for cfg in os_cfg:
        install(configure.build_dir(build_type, cfg), configure.utils_dir, cfg)
