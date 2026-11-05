#!/usr/bin/env python3
"""Force buildtool's Procedure._check_network to the retry-based implementation.

The apollo-neo-buildtool package installs a single-attempt _check_network
(one curl call, 5s timeout, no retry), so any transient network hiccup
flips buildtool into permanent offline mode for the rest of the run. We
want the retry version below instead (7 attempts, 15s timeout each).

It also keeps buildtool offline by default (BUILDTOOL_ONLINE=1 to go
online): its dependencies come from Hugging Face, not the Baidu server.

This replaces the whole method body unconditionally (matched by its
`def _check_network(self):` header up to the next method at the same
indentation), regardless of exactly how the currently installed body is
written. Re-applied on every container (re)creation because
`apt install --only-upgrade` reinstalls the single-attempt version every
time. Safe to run repeatedly (idempotent).
"""
import glob
import re

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/core/task/bazel/handler/__init__.py"

FIXED_BODY = '''    def _check_network(self):
        # The server (apollo.baidu.com) is in mainland China and slow
        # from elsewhere; everything buildtool needs is restored from Hugging
        # Face, so stay offline unless BUILDTOOL_ONLINE=1 asks for the server.
        if os.environ.get("BUILDTOOL_ONLINE") != "1":
            logger.info("Offline mode (set BUILDTOOL_ONLINE=1 to use the server)")
            self.online = False
            return
        login_api = get_config("api", "login")
        cmd = ["curl", "--max-time", "15", login_api, ">/dev/null 2>&1"]
        max_attempts = 7
        for attempt in range(1, max_attempts + 1):
            if subprocess.call(" ".join(cmd), shell=True) == 0:
                return
            logger.warning(
                "Network check attempt {}/{} failed".format(attempt, max_attempts))
        logger.warning("Can't connect with the server, use offline mode")
        self.online = False

'''

# Captures the whole current _check_network body: from its `def` line up to
# (but not including) the next method defined at the same 4-space class
# indentation, so it doesn't care how the body in between is written.
METHOD_PATTERN = re.compile(
    r"    def _check_network\(self\):\n"
    r"(?:(?!    def ).*\n)*"
)


def patch_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if FIXED_BODY in content:
        print("[skip] already the retry version: {}".format(path))
        return

    new_content, count = METHOD_PATTERN.subn(FIXED_BODY, content, count=1)
    if count == 0:
        print("[warn] _check_network method not found, skip: {}".format(path))
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("[fixed] {}".format(path))


def main():
    targets = glob.glob(TARGET_GLOB)
    if not targets:
        print("[warn] no buildtool handler/__init__.py matched {}".format(TARGET_GLOB))
        return
    for path in targets:
        patch_file(path)


if __name__ == "__main__":
    main()
