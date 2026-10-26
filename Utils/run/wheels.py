#!/usr/bin/env python3

# wheels.py writes wheels.txt, the pins of requirements.txt with the hashes
# of the wheels that bundle.py installs for each target, as PyPI lists them.

import json
import re
import subprocess
import sys
sys.dont_write_bytecode = True

import bundle

# The normalized name of a package (PEP 503).
def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()

if __name__ == "__main__":
    hashes = {}
    for target in bundle.targets.values():
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--dry-run", "--ignore-installed", "--report", "-",
            "-r", bundle.requirements_txt] + bundle.pip_options(target), stdout = subprocess.PIPE, text = True)
        if r.returncode:
            sys.exit("pip exited with status " + str(r.returncode))
        for wheel in json.loads(r.stdout)["install"]:
            name = canonical(wheel["metadata"]["name"])
            hashes.setdefault(name, set()).add(wheel["download_info"]["archive_info"]["hashes"]["sha256"])
    lines = ["# The wheels that bundle.py installs, pinned by hash. Utils/run/wheels.py",
        "# writes this file from requirements.txt."]
    for pin in bundle.pins(bundle.requirements_txt):
        lines.append(pin + " \\")
        lines.append(" \\\n".join("    --hash=sha256:" + h for h in sorted(hashes[canonical(pin.split("==")[0])])))
    with open(bundle.wheels_txt, "w") as f:
        f.write("\n".join(lines) + "\n")
