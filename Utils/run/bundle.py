#!/usr/bin/env python3

import glob
import gzip
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
sys.dont_write_bytecode = True
import tarfile
import time
import urllib.request
import zipfile

import configure
import install

usage = "usage: bundle.py lin|win|mac-arm64|mac-x86_64"

root_dir = os.path.dirname(configure.utils_dir)
build_dir = os.path.join(configure.utils_dir, "build")

python_url = "https://github.com/astral-sh/python-build-standalone/releases/download/20261021/"
python_version = "3.15"

targets = {
    "lin": {
        "cfg": configure.lin_cfg,
        "python": "cpython-3.15.1+20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "sha256": "01d901e09ca673c99a6c9bd9ec2cfbd58dbe8813d07514c36cb73cfd20b18bd8",
        "site": "python/lib/python3.15/site-packages",
        "platform": "manylinux_2_28_x86_64",
        "launcher": "Ortho4XP",
        "archive": "linux-x86_64.tar.gz",
    },
    "win": {
        "cfg": configure.win_cfg,
        "python": "cpython-3.15.1+20261021-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "sha256": "e7153a9f05921a72f7da1551d3bd374a88c4ffc0bbbea08776ddcb99b787d0f8",
        "site": "python/Lib/site-packages",
        "platform": "win_amd64",
        "launcher": "Ortho4XP.cmd",
        "archive": "windows-x86_64.zip",
    },
    # The pyproj wheels need macOS 14 on Apple Silicon and 15 on Intel.
    "mac-arm64": {
        "cfg": configure.mac_cfg,
        "python": "cpython-3.15.1+20261021-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "1a4eb4d5aa82400de6eeb94e6fc6d870f5fabc97eb1d801f7eeba8072117b11c",
        "site": "python/lib/python3.15/site-packages",
        "platform": "macosx_14_0_arm64",
        "launcher": "Ortho4XP",
        "archive": "macos-arm64.tar.gz",
        "arch": "arm64",
    },
    "mac-x86_64": {
        "cfg": configure.mac_cfg,
        "python": "cpython-3.15.1+20261021-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "sha256": "b7604b6cfcce467baca5bd607c4fc55928be99310b92b4faf2bdb448f488929e",
        "site": "python/lib/python3.15/site-packages",
        "platform": "macosx_15_0_x86_64",
        "launcher": "Ortho4XP",
        "archive": "macos-x86_64.tar.gz",
        "arch": "x86_64",
    },
}

seven_zip_url = "https://github.com/ip7z/7zip/releases/download/26.05/"
seven_zip = {
    "lin": ("7z2605-linux-x64.tar.xz", "22cf82fcc6eb7474f1d5bb4f02c0f8ebd573a718089d6833dc30f8350a3b232e"),
    "mac": ("7z2605-mac.tar.xz", "12ef6830317743cfd3f0ab1724bde43fcc270d8b6d8564f8c12c2a2b7f993570"),
    "win": ("7z2605-x64.exe", "fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0"),
}

# The launchers keep the user's Python and Tcl settings away from the
# bundled Python.
launchers = {
    "Ortho4XP": "#!/bin/sh\n"
        "# Ortho4XP [lat lon [imagery zl]]: run Ortho4XP.py with the bundled Python\n"
        "dir=$(dirname \"$0\")\n"
        "unset TCL_LIBRARY TK_LIBRARY\n"
        "exec \"$dir/python/bin/python3\" -s -E \"$dir/Ortho4XP.py\" \"$@\"\n",
    "Ortho4XP.cmd": "@echo off\r\n"
        "rem Ortho4XP [lat lon [imagery zl]]: run Ortho4XP.py with the bundled Python\r\n"
        "setlocal\r\n"
        "set TCL_LIBRARY=\r\n"
        "set TK_LIBRARY=\r\n"
        "\"%~dp0python\\python.exe\" -s -E \"%~dp0Ortho4XP.py\" %*\r\n",
}

# Windows provides these DLLs, and the api-ms-win-* API sets.
system_dlls = {
    "advapi32.dll", "bcrypt.dll", "comctl32.dll", "comdlg32.dll",
    "crypt32.dll", "gdi32.dll", "glu32.dll", "imm32.dll", "iphlpapi.dll",
    "kernel32.dll", "msvcrt.dll", "netapi32.dll", "ntdll.dll",
    "ole32.dll", "oleaut32.dll", "opengl32.dll", "propsys.dll",
    "psapi.dll", "rpcrt4.dll", "secur32.dll", "shell32.dll",
    "shlwapi.dll", "user32.dll", "userenv.dll", "version.dll",
    "winmm.dll", "winspool.drv", "ws2_32.dll",
}

def files(top):
    for dir, dirs, names in os.walk(top):
        dirs.sort()
        for name in sorted(names):
            yield os.path.join(dir, name)

def download(url, sha256):
    path = os.path.join(build_dir, "download", url.rsplit("/", 1)[1])
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok = True)
        print("Downloading " + url)
        with urllib.request.urlopen(url) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != sha256:
        sys.exit(path + ": SHA-256 is " + digest + ", want " + sha256)
    return path

def file_version(path):
    with open(path, "rb") as f:
        data = f.read()
    # VS_FIXEDFILEINFO: signature, structure version, then the version.
    i = data.find(struct.pack("<I", 0xFEEF04BD))
    (ms, ls) = struct.unpack_from("<II", data, i + 8)
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)

# The programs outside python/ need at most glibc 2.28, like the
# manylinux_2_28 wheels. medit links the system's OpenGL dynamically,
# and building it on a current glibc makes it need 2.34.
def check_elf(top):
    errors = []
    for path in files(top):
        if os.path.relpath(path, top).startswith("python" + os.sep):
            continue
        with open(path, "rb") as f:
            if f.read(4) != b"\x7fELF":
                continue
        most = [2, 34] if os.path.basename(path) == "medit" else [2, 28]
        out = subprocess.run(["objdump", "-T", path], capture_output = True, text = True).stdout
        for version in set(re.findall(r"GLIBC_([0-9.]+)", out)):
            if [int(n) for n in version.split(".")] > most:
                errors.append(path + " needs GLIBC_" + version)
    return errors

def check_pe(top):
    shipped = {os.path.basename(path).lower() for path in files(top) if path.lower().endswith(".dll")}
    errors = []
    for path in files(top):
        if not path.lower().endswith((".exe", ".dll", ".pyd")):
            continue
        out = subprocess.run(["x86_64-w64-mingw32-objdump", "-p", path], capture_output = True, text = True).stdout
        for dll in re.findall(r"DLL Name: (\S+)", out):
            dll = dll.lower()
            if dll not in shipped and dll not in system_dlls and not dll.startswith("api-ms-win-"):
                errors.append(path + " needs " + dll)
    return errors

# Puts 7-Zip in utils: the static 7zzs as 7zz for Linux, the universal
# 7zz for macOS, and for Windows 7z.exe with 7z.dll, which 7zzs unpacks
# from the installer.
def install_7zip(stage, utils, system):
    (name, sha256) = seven_zip["mac" if system == "mac" else "lin"]
    program = "7zz" if system == "mac" else "7zzs"
    with tarfile.open(download(seven_zip_url + name, sha256)) as tar:
        tar.extractall(stage, members = [tar.getmember(program), tar.getmember("License.txt")], filter = "data")
    if system == "win":
        (name, sha256) = seven_zip["win"]
        configure.run([os.path.join(stage, program), "x", "-y", "-bso0", "-o" + utils, download(seven_zip_url + name, sha256), "7z.exe", "7z.dll"])
    else:
        shutil.copy2(os.path.join(stage, program), os.path.join(utils, "7zz"))
    shutil.copy2(os.path.join(stage, "License.txt"), os.path.join(utils, "7-Zip-License.txt"))

# The programs outside python/ hold arch and link only the libraries and
# frameworks of macOS.
def check_macho(top, arch):
    errors = []
    for path in files(top):
        if os.path.relpath(path, top).startswith("python" + os.sep) or os.path.islink(path):
            continue
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
            continue
        archs = subprocess.run(["lipo", "-archs", path], capture_output = True, text = True).stdout.split()
        if arch not in archs:
            errors.append(path + " holds " + " ".join(archs) + ", want " + arch)
        out = subprocess.run(["otool", "-L", "-arch", arch, path], capture_output = True, text = True).stdout
        for lib in out.splitlines()[1:]:
            lib = lib.split()[0]
            if not lib.startswith(("/usr/lib/", "/System/Library/")):
                errors.append(path + " needs " + lib)
    return errors

def write_tar(path, top, mtime):
    def reset(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = mtime
        return info
    with gzip.GzipFile(path, "wb", mtime = mtime) as f:
        with tarfile.open(fileobj = f, mode = "w") as tar:
            tar.add(top, arcname = os.path.basename(top), filter = reset)

def write_zip(path, top, mtime):
    date_time = time.gmtime(mtime)[:6]
    with zipfile.ZipFile(path, "w") as z:
        for file in files(top):
            name = os.path.relpath(file, os.path.dirname(top)).replace(os.sep, "/")
            info = zipfile.ZipInfo(name, date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (os.stat(file).st_mode & 0xFFFF) << 16
            with open(file, "rb") as f, z.open(info, "w") as w:
                shutil.copyfileobj(f, w)

def git(*args):
    r = subprocess.run(["git", "-C", root_dir] + list(args), stdout = subprocess.PIPE, text = True)
    if r.returncode:
        sys.exit("git exited with status " + str(r.returncode))
    return r.stdout.strip()

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in targets:
        print(usage, file = sys.stderr)
        sys.exit(2)
    target = targets[sys.argv[1]]
    cfg = target["cfg"]
    stage = os.path.join(build_dir, "bundle", cfg["dir"])
    top = os.path.join(stage, "Ortho4XP")
    shutil.rmtree(stage, ignore_errors = True)
    os.makedirs(stage)

    tree = os.path.join(stage, "tree.tar")
    git("archive", "--prefix=Ortho4XP/", "-o", tree, "HEAD")
    with tarfile.open(tree) as tar:
        tar.extractall(stage, filter = "data")
    os.remove(tree)
    install.install(configure.build_dir("Release", cfg), os.path.join(top, "Utils"), cfg)
    install_7zip(stage, os.path.join(top, "Utils", cfg["dir"]), cfg["dir"])

    with tarfile.open(download(python_url + target["python"], target["sha256"])) as tar:
        tar.extractall(top, filter = "data")
    site = os.path.join(top, target["site"])
    configure.run([sys.executable, "-m", "pip", "install", "--quiet", "--root-user-action=ignore", "--no-deps", "--no-compile",
        "--only-binary=:all:", "--platform", target["platform"], "--implementation", "cp",
        "--python-version", python_version, "--abi", "cp" + python_version.replace(".", ""),
        "--target", site, "-r", os.path.join(root_dir, "requirements.txt")])
    shutil.rmtree(os.path.join(site, "bin"), ignore_errors = True)
    if cfg["dir"] == "win":
        # The Rtree DLLs need msvcp140.dll from the Visual C++ runtime,
        # which other wheels ship under hashed names.
        msvcp = max(glob.glob(os.path.join(site, "*.libs", "msvcp140-*.dll")), key = file_version)
        shutil.copy2(msvcp, os.path.join(site, "rtree", "lib", "msvcp140.dll"))

    launcher = os.path.join(top, target["launcher"])
    with open(launcher, "w", newline = "") as f:
        f.write(launchers[target["launcher"]])
    os.chmod(launcher, 0o755)

    if cfg["dir"] == "win":
        errors = check_pe(top)
    elif cfg["dir"] == "mac":
        errors = check_macho(top, target["arch"])
    else:
        errors = check_elf(top)
    if errors:
        sys.exit("\n".join(errors))

    archive = os.path.join(build_dir, "Ortho4XP-" + git("describe", "--tags", "--always") + "-" + target["archive"])
    mtime = int(git("log", "-1", "--no-show-signature", "--format=%ct"))
    if archive.endswith(".zip"):
        write_zip(archive, top, mtime)
    else:
        write_tar(archive, top, mtime)
    print(archive)
