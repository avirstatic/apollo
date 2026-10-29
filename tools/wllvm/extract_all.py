#!/usr/bin/env python3
"""Link LLVM IR for every built cc_binary and cc_test, plus its CUDA device IR.

Apollo components are cc_binary(linkshared = True, linkstatic = False): the
component .so (and many executables) hold little code themselves and load
each cc_library from bazel-bin/_solib_*. extract-bc only reads the input's own
.llvm_bc section, so this tool gathers the sections of the binary and of every
Bazel-built shared library in its ldd closure, de-duplicates them, and links
<output>/<package>/<name>.bc (CPU and CUDA host IR). CUDA host captures found
among those inputs contribute device modules, linked per GPU architecture into
<output>/<package>/<name>.device.<arch>.bc. index.json records every target.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading


DEFAULT_SCOPE = ("kind('cc_(binary|test) rule', //modules/... + //cyber/...) "
                 "except //modules/drivers/canbus/can_client/esd/...")


def query_targets(bazel, scope):
    # --nofetch: use the external repositories as the build left them. query
    # does not get the build's --action_env and would regenerate
    # @local_config_cuda without CUDA.
    output = subprocess.check_output([bazel, "query", "--nofetch", scope],
                                     universal_newlines=True)
    return [line.strip() for line in output.splitlines() if line.startswith("//")]


def label_path(bazel_bin, label):
    package, _, name = label[2:].partition(":")
    return bazel_bin / package / name


def section_paths(binary, objcopy):
    """Return the paths listed in a binary's .llvm_bc section, or None."""
    with tempfile.NamedTemporaryFile(prefix="llvm-bc-section-") as section:
        result = subprocess.run(
            [objcopy, "--dump-section", ".llvm_bc=" + section.name,
             str(binary), os.devnull],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            return None
        data = Path(section.name).read_bytes().decode("utf-8", "replace")
    # The linker pads concatenated sections with NUL bytes.
    return [line.strip("\0").strip() for line in data.split("\n")
            if line.strip("\0").strip()]


def bazel_libraries(binary, bazel_bin):
    """Bazel-built shared libraries in the binary's dynamic dependency closure.

    Kept in ldd's (the loader's breadth-first) order: for a symbol defined in
    several libraries, the first one in this order is what the loader binds.
    """
    result = subprocess.run(["ldd", str(binary)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    libraries = []
    for line in result.stdout.splitlines():
        match = re.search(r"=>\s+(\S+)\s+\(", line)
        if match:
            library = Path(os.path.realpath(match.group(1)))
            if (str(library).startswith(str(bazel_bin) + os.sep) and
                    library not in libraries):
                libraries.append(library)
    return libraries


def resolve(path, store):
    """Same fallback as extract-bc: the original path, else its hash in the store."""
    if os.path.isfile(path):
        return os.path.realpath(path)
    stored = store / hashlib.sha256(path.encode("utf-8")).hexdigest()
    return str(stored) if stored.is_file() else None


def cuda_captures(paths, store):
    """Map CUDA host.bc paths back to their capture manifests."""
    captures, seen = [], set()
    cuda_root = (store / "cuda").resolve()
    for path in paths:
        host = Path(path)
        if host.name != "host.bc":
            continue
        try:
            host.resolve().relative_to(cuda_root)
        except ValueError:
            continue
        # A capture reached both statically and via a shared library is one TU.
        if host.resolve() in seen:
            continue
        seen.add(host.resolve())
        manifest_path = host.parent / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("status") == "complete":
                captures.append(manifest)
    return captures


def run_link(command):
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, universal_newlines=True)
    return result.returncode, result.stdout[-4000:]


def definers(symbol, inputs, llvm_nm):
    """Inputs that define symbol, in link order."""
    found = []
    needle = symbol.encode("utf-8")
    for path in inputs:
        # Cheap byte search first; llvm-nm only on candidate modules.
        if needle not in Path(path).read_bytes():
            continue
        result = subprocess.run([llvm_nm, "--defined-only", str(path)],
                                stdout=subprocess.PIPE, universal_newlines=True)
        if any(line.split()[-1] == symbol for line in result.stdout.splitlines()
               if line.strip()):
            found.append(path)
    return found


def link(llvm_link, inputs, output, libdevice=None, duplicates=None):
    """llvm-link inputs; duplicate strong symbols keep their first definition.

    Inputs are in link order (within a library) and loader order (across
    libraries), so the first definer is what ld --allow-multiple-definition
    keeps, or what the dynamic loader binds. llvm-link rejects duplicates, so on
    that error the first definer is re-linked with --override (its definitions
    replace the others') and the link is retried. Resolved symbols are appended
    to duplicates.
    """
    llvm_nm = str(Path(llvm_link).with_name("llvm-nm"))
    overrides = []
    while True:
        regular = [x for x in inputs if x not in overrides]
        with tempfile.NamedTemporaryFile("w", prefix="llvm-link-", suffix=".rsp") as rsp:
            # A response file: whole programs reach thousands of modules.
            rsp.write("\n".join(str(x) for x in regular) + "\n")
            rsp.write("".join("--override={}\n".format(x) for x in overrides))
            rsp.flush()
            code, log = run_link([llvm_link, "-o", str(output), "@" + rsp.name])
        match = re.search(r"Linking globals named '([^']+)': symbol multiply defined", log)
        if code == 0 or not match or len(overrides) > 50:
            break
        symbol = match.group(1)
        found = definers(symbol, inputs, llvm_nm)
        if len(found) < 2:
            break
        # Later definers beyond the second would still collide among the regular
        # inputs; override them too, with the first definer applied last.
        updated = []
        for x in overrides + found[1:-1]:
            if x != found[0] and x not in updated:
                updated.append(x)
        updated.append(found[0])
        if updated == overrides:
            break
        overrides = updated
        if duplicates is not None:
            duplicates.append({"symbol": symbol, "kept": str(found[0]),
                               "dropped": [str(x) for x in found[1:]]})
    if code != 0 or not libdevice:
        return code, log
    # --only-needed applies to every input after the first, so libdevice is
    # linked in a second step against the already merged module.
    return run_link([llvm_link, "-o", str(output), str(output),
                     "--only-needed", str(libdevice)])


def extract_target(label, args):
    binary = label_path(args.bazel_bin, label)
    entry = {"label": label, "binary": str(binary)}
    if not binary.is_file():  # not built (a same-named directory is not a binary)
        entry["status"] = "not_built"
        return entry
    destination = args.output / label[2:].partition(":")[0]
    destination.mkdir(parents=True, exist_ok=True)
    name = label.partition(":")[2]

    sources = [binary.resolve()] + bazel_libraries(binary, args.bazel_bin)
    paths, without_section = [], []
    for source in sources:
        listed = section_paths(source, args.objcopy)
        if listed is None:
            without_section.append(str(source))
        else:
            paths.extend(listed)
    entry["shared_libraries"] = len(sources) - 1
    entry["libraries_without_bitcode"] = without_section

    inputs, missing = [], []
    for path in paths:
        resolved = resolve(path, args.store)
        if resolved is None:
            missing.append(path)
        elif resolved not in inputs:
            inputs.append(resolved)
    entry["bitcode_inputs"] = len(inputs)
    entry["missing_inputs"] = missing
    if not inputs:
        entry["status"] = "no_bitcode"
        return entry

    output = destination / (name + ".bc")
    (destination / (name + ".bc.manifest")).write_text("\n".join(inputs) + "\n")
    duplicates = []
    code, log = link(args.llvm_link, inputs, output, duplicates=duplicates)
    entry["host_bc"] = str(output)
    entry["status"] = "ok" if code == 0 and not missing else "failed"
    if code != 0:
        entry["log"] = log

    captures = cuda_captures(paths, args.store)
    if captures:
        entry["cuda_sources"] = sorted({c["source"] for c in captures})
        entry["device_bc"] = {}
        arches = sorted({a for c in captures for a in c["device_bitcode"]})
        for arch in arches:
            device_inputs = [c["device_bitcode"][arch] for c in captures
                             if arch in c["device_bitcode"]]
            device = destination / "{}.device.{}.bc".format(name, arch)
            code, log = link(args.llvm_link, device_inputs, device, args.libdevice,
                             duplicates)
            entry["device_bc"][arch] = str(device) if code == 0 else None
            if code != 0:
                entry["status"] = "failed"
                entry["device_log"] = log
    # Symbols defined more than once, resolved like ld --allow-multiple-definition
    entry["duplicate_symbols"] = duplicates
    return entry


def default_jobs():
    """Half the CPUs, capped so each job has ~3 GB of 70% of available memory.

    The largest Apollo target peaks at about 2.4 GB in llvm-link.
    """
    available_kb = 0
    with open("/proc/meminfo") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable:"):
                available_kb = int(line.split()[1])
    by_memory = int(available_kb * 0.7 / (3 * 2**20))
    return max(1, min((os.cpu_count() or 2) // 2, by_memory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store", type=Path,
                        default=Path(os.environ.get("WLLVM_BC_STORE", "/apollo/wllvm_bc")))
    parser.add_argument("--bazel-bin", type=Path, default=Path("/apollo/bazel-bin"))
    parser.add_argument("--targets", type=Path,
                        help="File of labels; default queries cc_binary and cc_test rules")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--llvm-link", default="/opt/llvm-14/bin/llvm-link")
    parser.add_argument("--objcopy", default="/opt/llvm-14/bin/llvm-objcopy")
    parser.add_argument("--libdevice", type=Path,
                        help="Link needed libdevice functions into device modules, "
                             "e.g. /usr/local/cuda/nvvm/libdevice/libdevice.10.bc")
    parser.add_argument("--jobs", type=int, default=0,
                        help="Targets linked in parallel; default from CPUs and memory")
    args = parser.parse_args()
    args.output = args.output.absolute()
    args.store = args.store.absolute()
    args.bazel_bin = args.bazel_bin.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.exit(1, "Output directory is not empty; use an empty one to avoid "
                       "mixing extractions\n")
    for tool in (args.llvm_link, args.objcopy):
        if not os.access(tool, os.X_OK):
            parser.exit(1, "Required tool not found: {}\n".format(tool))

    if args.targets:
        labels = [l.strip() for l in args.targets.read_text().splitlines()
                  if l.strip().startswith("//")]
    else:
        labels = query_targets(args.bazel, args.scope)
    args.output.mkdir(parents=True, exist_ok=True)

    jobs = args.jobs or default_jobs()
    print("Linking {} targets with {} jobs".format(len(labels), jobs),
          file=sys.stderr, flush=True)
    results = {}
    lock = threading.Lock()

    def run(label):
        try:
            entry = extract_target(label, args)
        except Exception as error:  # keep the other targets going
            entry = {"label": label, "status": "failed", "log": repr(error)}
        with lock:
            results[label] = entry
            print("[{}/{}] {} {}".format(len(results), len(labels), entry["status"],
                                         label), file=sys.stderr, flush=True)

    # Each target links independently into its own files, so the outputs do
    # not depend on the order in which targets finish.
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(run, labels))
    # A link killed for memory under parallel load gets one serial retry.
    for label in [l for l in labels if results[l]["status"] == "failed"]:
        print("Retrying {}".format(label), file=sys.stderr, flush=True)
        results.pop(label)
        run(label)
    index = [results[label] for label in labels]
    (args.output / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    counts = {}
    for entry in index:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0 if counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
