#!/usr/bin/env python3
"""Keep buildtool's installed packages usable across dev containers.

buildtool installs the Apollo third-party packages (apollo-neo-*) with apt
into /opt/apollo/neo, which aem keeps in the opt volume; a few of
their apt dependencies (e.g. libnuma-dev) land outside it. buildtool decides
what is installed from dpkg (`dpkg -l`), and a new container starts with the
image's dpkg database, so the files alone are not enough. This saves, next to
the files in the volume, what a new container needs:

  /opt/apollo/neo/.neo_deps/
    status   dpkg status stanzas of the packages installed after the image
    info/    their /var/lib/dpkg/info files
    debs/    .deb files of those packages that install outside /opt/apollo/neo
    files    what to publish: the packages' files under /opt/apollo/neo
    VERSION  name of the published archive

buildtool also installs what it builds from source into /opt/apollo/neo
(bin/, lib/modules/, share/packages/, ...). Those are left out of `files`:
only the apollo-neo-* packages' own files (dpkg -L), other files in their
packages/<name>/ directories (written by their postinst scripts) and
symlinks into those directories (latest, lib/3rd-*, setup.sh) are listed.
Packages that earlier containers installed into the volume have no dpkg
record in this one; their packages/<name>/ directories are listed as well.

  save    --baseline <image's /var/lib/dpkg/status> --version <archive name>
          (run as root, after a build)
  restore                                              (run as root, new container)
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

NEO = "/opt/apollo/neo"
STATE = NEO + "/.neo_deps"
DPKG_STATUS = "/var/lib/dpkg/status"
DPKG_INFO = "/var/lib/dpkg/info"
APT_CACHE = "/var/cache/apt/archives"
NEO_PREFIX = "apollo-neo-"


def read_stanzas(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    stanzas = {}
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n")
                      if ": " in line and not line.startswith(" "))
        name = fields.get("Package")
        if name:
            stanzas[(name, fields.get("Architecture", ""))] = (fields, block)
    return stanzas


def installed(fields):
    return fields.get("Status") == "install ok installed"


def info_files(name, arch):
    names = {name}
    if arch and arch != "all":
        names.add("{}:{}".format(name, arch))
    files = []
    for n in names:
        files += glob.glob(os.path.join(DPKG_INFO, n + ".*"))
    return files


def neo_files(names):
    """Paths (relative to NEO) of the given apollo-neo-* packages' files."""
    owned = set()
    for name in names:
        # packages without a dpkg record (see save) have only their directory
        out = subprocess.run(["dpkg", "-L", name], capture_output=True, text=True)
        if out.returncode == 0:
            owned.update(p for p in out.stdout.splitlines() if p.startswith(NEO + "/"))
    short = {n[len(NEO_PREFIX):] for n in names}
    pkg_dir = os.path.join(NEO, "packages") + "/"

    def in_package(path):
        if not path.startswith(pkg_dir):
            return False
        return path[len(pkg_dir):].split("/", 1)[0] in short

    files = []
    for root, dirs, entries in os.walk(NEO):
        dirs[:] = [d for d in dirs if d != "__pycache__"
                   and os.path.join(root, d) != STATE]
        # symlinked directories are not descended into; list them as links
        for e in entries + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            path = os.path.join(root, e)
            if (path in owned or in_package(path) or (os.path.islink(path)
                    and in_package(os.path.normpath(os.path.join(root, os.readlink(path)))))):
                files.append(os.path.relpath(path, NEO))
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return sorted(files)


def deb_index(dirs):
    """{(package, version): path} of the .deb files in dirs."""
    index = {}
    for d in dirs:
        for path in glob.glob(os.path.join(d, "*.deb")):
            out = subprocess.run(["dpkg-deb", "-f", path, "Package", "Version"],
                                 capture_output=True, text=True)
            fields = dict(l.split(": ", 1) for l in out.stdout.splitlines() if ": " in l)
            if out.returncode == 0 and "Package" in fields:
                index.setdefault((fields["Package"], fields.get("Version")), path)
    return index


def save(baseline, version):
    # packages the image does not have, or has in another version: apt
    # upgrades some image packages (libcairo2, libxml2, ...) to satisfy the
    # new ones, and a new container starts with the image's old versions
    base = {k: f.get("Version") for k, (f, _) in read_stanzas(baseline).items()
            if installed(f)}
    added = [(k, f, b) for k, (f, b) in read_stanzas(DPKG_STATUS).items()
             if installed(f) and base.get(k) != f.get("Version")]
    # keep the .debs we already have (from an earlier save, or restored from
    # the published archive): only what neither they nor apt's cache hold is
    # downloaded again
    old = STATE + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(STATE):
        os.rename(STATE, old)
    known = deb_index([os.path.join(old, "debs"), APT_CACHE])
    os.makedirs(os.path.join(STATE, "info"))
    os.makedirs(os.path.join(STATE, "debs"))
    neo, other, missing = [], [], []
    for (name, arch), fields, block in sorted(added):
        if name.startswith(NEO_PREFIX):
            neo.append(block)
            for path in info_files(name, arch):
                shutil.copy2(path, os.path.join(STATE, "info"))
        elif (name, fields["Version"]) in known:
            shutil.copy2(known[(name, fields["Version"])], os.path.join(STATE, "debs"))
            other.append("{}={}".format(name, fields["Version"]))
        else:
            other.append("{}={}".format(name, fields["Version"]))
            missing.append(other[-1])
    with open(os.path.join(STATE, "status"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(neo) + "\n\n" if neo else "")
    if missing:
        # these install outside /opt/apollo/neo (system dirs), so keep their
        # .debs and reinstall them offline in a new container
        subprocess.run(["apt-get", "download", "-q"] + missing,
                       cwd=os.path.join(STATE, "debs"), check=True)
    shutil.rmtree(old, ignore_errors=True)
    with open(os.path.join(STATE, "VERSION"), "w", encoding="utf-8") as f:
        f.write(version + "\n")
    names = [name for (name, _), _f, _b in added if name.startswith(NEO_PREFIX)]
    # A container keeps the opt volume of the containers before it, but not
    # their dpkg records: packages they installed have only their packages/
    # directory, which buildtool takes as installed (deprecated_package_path)
    in_image = {name for (name, _) in base}
    for d in sorted(os.listdir(os.path.join(NEO, "packages"))):
        if NEO_PREFIX + d not in names and NEO_PREFIX + d not in in_image:
            names.append(NEO_PREFIX + d)
    files = neo_files(names)
    for root, _, entries in os.walk(STATE):
        files += [os.path.relpath(os.path.join(root, e), NEO) for e in entries]
    files.append(os.path.relpath(os.path.join(STATE, "files"), NEO))
    with open(os.path.join(STATE, "files"), "w", encoding="utf-8") as f:
        f.write("\n".join(files) + "\n")
    print("saved {} apollo-neo packages ({} with dpkg records, {} files), "
          "{} other packages ({} downloaded): {}"
          .format(len(names), len(neo), len(files), len(other), len(missing), " ".join(other)))


def restore():
    if not os.path.isdir(STATE):
        sys.exit("no saved package state in {}".format(STATE))
    debs = sorted(glob.glob(os.path.join(STATE, "debs", "*.deb")))
    if debs:
        subprocess.run(["dpkg", "-i", "--skip-same-version"] + debs, check=True)
    current = read_stanzas(DPKG_STATUS)
    saved = read_stanzas(os.path.join(STATE, "status"))
    missing = [b for k, (f, b) in sorted(saved.items())
               if k not in current or not installed(current[k][0])]
    for path in glob.glob(os.path.join(STATE, "info", "*")):
        dst = os.path.join(DPKG_INFO, os.path.basename(path))
        if not os.path.exists(dst):
            shutil.copy2(path, dst)
    if missing:
        # drop stale entries for the same packages, then append the saved ones
        names = {k for k in saved}
        kept = [b for k, (f, b) in current.items() if k not in names]
        with open(DPKG_STATUS + ".neo-deps-new", "w", encoding="utf-8") as f:
            f.write("\n\n".join(kept + [b for _, (f_, b) in sorted(saved.items())]) + "\n")
        shutil.copy2(DPKG_STATUS, DPKG_STATUS + "-old")
        os.replace(DPKG_STATUS + ".neo-deps-new", DPKG_STATUS)
    print("restored {} .debs, {} apollo-neo dpkg records ({} were missing)".format(
        len(debs), len(saved), len(missing)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("save")
    p.add_argument("--baseline", required=True)
    p.add_argument("--version", required=True)
    sub.add_parser("restore")
    args = parser.parse_args()
    if args.cmd == "save":
        save(args.baseline, args.version)
    else:
        restore()


if __name__ == "__main__":
    main()
