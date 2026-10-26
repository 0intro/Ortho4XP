#!/bin/sh
# install_mac.sh: install what Ortho4XP needs with Homebrew, make a Python
# environment in venv, and build the programs in Utils/mac. Run it from the
# Ortho4XP folder, then start Ortho4XP with venv/bin/python Ortho4XP.py.
set -eu

if ! command -v brew >/dev/null; then
	echo 'install_mac.sh: Homebrew is missing, see https://brew.sh' 1>&2
	exit 1
fi
if [ ! -f Ortho4XP.py ]; then
	echo 'install_mac.sh: run it from the Ortho4XP folder' 1>&2
	exit 1
fi

brew install python@3.14 python-tk@3.14 sevenzip
command -v cmake >/dev/null || brew install cmake
"$(brew --prefix python@3.14)/bin/python3.14" -m venv venv
venv/bin/pip install -r requirements.txt
for step in configure build install; do
	venv/bin/python "Utils/run/$step.py" mac
done
