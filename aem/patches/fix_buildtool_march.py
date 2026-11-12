#!/usr/bin/env python3
"""Build for the generic x86-64 CPU instead of the building machine's.

buildtool appends --copt and --host_copt with config/module.conf's march
(-march=native) to every Bazel command, after the options of tools/bazel.rc.
The machine's CPU then decides what the code and its LLVM IR target, and its
features change which code is compiled: with cx16, boost/atomic takes its
16-byte compare-and-swap path, which clang cannot compile for the CUDA device
side. -march=x86-64 is clang's default, so only tools/bazel.rc chooses the
instruction set (-mavx2).

Re-applied on every container creation, like the other buildtool patches.
Safe to run repeatedly (idempotent).
"""
import glob
import re

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/config/module.conf"

MARCH = re.compile(r"^march=.*$", re.MULTILINE)
GENERIC = "march=-march=x86-64"


def patch_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if re.search(r"^{}$".format(re.escape(GENERIC)), content, re.MULTILINE):
        print("[skip] already the generic x86-64 CPU: {}".format(path))
        return

    new_content, count = MARCH.subn(GENERIC, content, count=1)
    if count == 0:
        print("[warn] march setting not found, skip: {}".format(path))
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("[fixed] {}".format(path))


def main():
    targets = glob.glob(TARGET_GLOB)
    if not targets:
        print("[warn] no buildtool config/module.conf matched {}".format(TARGET_GLOB))
        return
    for path in targets:
        patch_file(path)


if __name__ == "__main__":
    main()
