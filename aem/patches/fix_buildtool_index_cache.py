#!/usr/bin/env python3
"""Let buildtool reuse its local package index instead of downloading it.

Whenever buildtool is online, CyberfileVersionDecider._init_metadata downloads
the full package index on every run and only compares it with the local
cache afterwards; the cache is used only when offline. On slow links to the
Baidu server that download alone takes minutes per run. With this patch the
index is never downloaded from Baidu any more: buildtool always uses the local
copy, which aem restores from Hugging Face when it creates the container
(see aem/publish_hf.sh). BUILDTOOL_REFRESH_INDEX=1 is the one way to
fetch it from Baidu again, to refresh that saved copy.

Re-applied on every container (re)creation because
`apt install --only-upgrade apollo-neo-buildtool` restores the original.
Safe to run repeatedly (idempotent).
"""
import glob
from pathlib import Path

TARGET_GLOB = "/opt/apollo/neo/packages/buildtool/*/core/version_decide/cyberfile/__init__.py"

GUARD_OLD = '''    def _init_metadata(self):
        raw_metadatas = None
        timeout_offline = False

        if self.online:
'''

HELPER_NEW = '''    def _index_download_needed(self):
        # Never download the package index from the Baidu server;
        # aem restores a saved copy. BUILDTOOL_REFRESH_INDEX=1 is the
        # only way to fetch it again (to refresh that saved copy).
        if os.environ.get("BUILDTOOL_REFRESH_INDEX") == "1":
            return True
        logger.info("Use the local package index; not downloading it from the "
                    "server (BUILDTOOL_REFRESH_INDEX=1 to refresh it)")
        return False
'''

GUARD_NEW = HELPER_NEW + '''
    def _init_metadata(self):
        raw_metadatas = None
        timeout_offline = False

        if self.online and self._index_download_needed():
'''


def patch_file(path):
    path = Path(path).resolve()
    original = path.read_text(encoding="utf-8")
    if GUARD_NEW in original:
        print("[skip] index cache already patched: {}".format(path))
        return
    if original.count(GUARD_OLD) == 1:
        content = original.replace(GUARD_OLD, GUARD_NEW, 1)
    else:
        raise RuntimeError(
            "Unrecognized buildtool index code in {}; refusing to patch".format(path))
    compile(content, str(path), "exec")
    backup = path.with_name(path.name + ".index_cache.orig")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    print("[fixed] index cache: {}".format(path))


def main():
    targets = sorted({Path(p).resolve() for p in glob.glob(TARGET_GLOB)})
    if not targets:
        raise RuntimeError("No buildtool index code found: {}".format(TARGET_GLOB))
    for path in targets:
        patch_file(path)


if __name__ == "__main__":
    main()
