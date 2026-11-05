#!/usr/bin/env python3
"""Make buildtool support both source-tree and package-workspace layouts.

Run after upgrading buildtool, just like fix_buildtool_network_check.py.
Patch the generator so subsequent WORKSPACE regeneration keeps the fix.
"""
import glob
from pathlib import Path

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/core/task/bazel/handler/__init__.py"

OPENSSL_OLD = '''            if os.path.join(os.path.dirname(workspace_file_path), "tools/external/openssl.BUILD"):
                grpc_init_content = grpc_init_content + 'new_local_repository(name = "boringssl",'
                grpc_init_content = grpc_init_content + ' build_file = "tools/external/openssl.BUILD",'
                grpc_init_content = grpc_init_content + ' path = "/opt/apollo/pkgs/openssl")\\n'
'''

OPENSSL_NEW = '''            # Accept both the full source tree and a package workspace.
            openssl_build_file = next((candidate for candidate in (
                "tools/external/openssl.BUILD", "third_party/openssl/openssl.BUILD"
            ) if os.path.isfile(os.path.join(
                os.path.dirname(workspace_file_path), candidate))), None)
            if openssl_build_file is not None:
                grpc_init_content += (
                    'new_local_repository(name = "boringssl",'
                    ' build_file = "' + openssl_build_file + '",'
                    ' path = "/opt/apollo/pkgs/openssl")\\n'
                )
'''

ROS_OLD = '''        if os.path.join(
            os.path.dirname(workspace_file_path), "tools/ros/ros_configure.bzl"):
'''

ROS_NEW = '''        if os.path.isfile(os.path.join(
            os.path.dirname(workspace_file_path), "tools/ros/ros_configure.bzl")):
'''

PROTO_OLD = '''            content = content.replace("@@REPLACE@@", src_value)
'''

PROTO_NEW = '''            # Full source workspaces must keep local proto deps.
            # The @@ marker selects source mode in proto.bzl; replacing it
            # with the last package path incorrectly redirects all other
            # packages to @apollo_src, shadowing their local proto inputs.
            if all(os.path.isfile(os.path.join(workspace, marker)) for marker in (
                "apollo.sh", "cyber/BUILD", "tools/workspace.bzl"
            )):
                src_value = "@@SOURCE@@"
            content = content.replace("@@REPLACE@@", src_value)
'''


def rewrite_file(path, replacements):
    path = Path(path).resolve()
    original = path.read_text(encoding="utf-8")
    content = original
    for old, new in replacements:
        if new in content:
            continue
        if content.count(old) != 1:
            raise RuntimeError(
                "Unrecognized buildtool generator in {}; refusing partial patch".format(path))
        content = content.replace(old, new, 1)
    compile(content, str(path), "exec")
    if content == original:
        print("[skip] workspace paths already patched: {}".format(path))
        return
    backup = path.with_name(path.name + ".workspace_paths.orig")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    print("[fixed] workspace paths: {}".format(path))


def patch_file(path):
    rewrite_file(path, ((OPENSSL_OLD, OPENSSL_NEW), (ROS_OLD, ROS_NEW)))


def main():
    targets = sorted({Path(p).resolve() for p in glob.glob(TARGET_GLOB)})
    if not targets:
        raise RuntimeError("No buildtool handlers found: {}".format(TARGET_GLOB))
    for path in targets:
        patch_file(path)
        rewrite_file(path.with_name("preprocess.py"), ((PROTO_OLD, PROTO_NEW),))


if __name__ == "__main__":
    main()
