#!/usr/bin/env python3
"""Make buildtool's apt install use the deb that buildtool has downloaded.

buildtool downloads each third-party package into its playground and then
runs `apt install --reinstall <deb>`. When an apt repository has the same
version, apt installs that one instead and downloads the package a second
time. Copying the deb into apt's archive cache first, under the file name apt
looks for, makes apt use it. If the copy fails, apt still downloads the
package itself.

Re-applied on every container creation, like fix_buildtool_network_check.py.
Safe to run repeatedly (idempotent).
"""
import glob
import re

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/core/task/bazel/handler/preprocess.py"

# The apt command for the downloaded deb in _install_apollo_package_in_playgroud
INSTALL_CMD = re.compile(
    r'        cmd = "\{\} \{\} \{\} 2>&1"\.format\(\n'
    r'            AptContext\.executable, " "\.join\(AptContext\.reinstall_args\), install_deb_path\)\n'
)

SEED_CACHE = r'''        cmd = ("sudo cp '{0}' /var/cache/apt/archives/"
               "$(dpkg-deb -f '{0}' Package)_"
               "$(dpkg-deb -f '{0}' Version | sed s/:/%3a/)_"
               "$(dpkg-deb -f '{0}' Architecture).deb; ").format(install_deb_path) + cmd
'''


def patch_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if SEED_CACHE in content:
        print("[skip] already seeds the apt cache: {}".format(path))
        return

    match = INSTALL_CMD.search(content)
    if match is None:
        print("[warn] apt install of the downloaded deb not found, skip: {}".format(path))
        return

    new_content = content[:match.end()] + SEED_CACHE + content[match.end():]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("[fixed] {}".format(path))


def main():
    targets = glob.glob(TARGET_GLOB)
    if not targets:
        print("[warn] no buildtool handler/preprocess.py matched {}".format(TARGET_GLOB))
        return
    for path in targets:
        patch_file(path)


if __name__ == "__main__":
    main()
