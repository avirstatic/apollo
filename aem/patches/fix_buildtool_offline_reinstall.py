#!/usr/bin/env python3
"""Let buildtool keep a third-party package offline when nothing is downloaded.

When an installed third-party package's version differs from the one a
cyberfile asks for (e.g. 3rd-fastrtps), buildtool marks it for "reinstall".
For third-party packages that branch downloads nothing unless latest_3rd_pkg
is set: it keeps the installed version and logs "reinstall ... successfully".
But it checks the network first, so in offline mode (the default, see
fix_buildtool_network_check.py) the whole build stops with NetworkIoError.
Check the network only when the branch will download.

Re-applied on every container (re)creation, like the other buildtool
patches. Safe to run repeatedly (idempotent).
"""
import glob
from pathlib import Path

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/core/task/bazel/handler/preprocess.py"

OLD = '''    elif status == reinstall:
        if not procedure.get_network_status():
            ErrCode.send_error(
'''

NEW = '''    elif status == reinstall:
        # Only check the network when this branch downloads: a third-party
        # package is kept as installed unless latest_3rd_pkg is set.
        needs_download = pkg_desc.type != "system" and (
            not _is_third_party_package(pkg_desc)
            or kwargs.get("latest_3rd_pkg") is True)
        if needs_download and not procedure.get_network_status():
            ErrCode.send_error(
'''


def patch_file(path):
    path = Path(path).resolve()
    original = path.read_text(encoding="utf-8")
    if NEW in original:
        print("[skip] offline reinstall already patched: {}".format(path))
        return
    if original.count(OLD) != 1:
        raise RuntimeError(
            "Unrecognized buildtool reinstall code in {}; refusing to patch".format(path))
    content = original.replace(OLD, NEW, 1)
    compile(content, str(path), "exec")
    backup = path.with_name(path.name + ".offline_reinstall.orig")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    print("[fixed] offline reinstall: {}".format(path))


def main():
    targets = sorted({Path(p).resolve() for p in glob.glob(TARGET_GLOB)})
    if not targets:
        raise RuntimeError("No buildtool preprocess code found: {}".format(TARGET_GLOB))
    for path in targets:
        patch_file(path)


if __name__ == "__main__":
    main()
