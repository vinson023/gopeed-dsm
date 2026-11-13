#!/usr/bin/env python3
"""Build Synology DSM 7 packages (.spk) for Gopeed, one per architecture.

Layout produced for each arch:

    build/gopeed-<arch>/
        INFO  LICENSE  PACKAGE_ICON*.PNG  package.tgz
        conf/  scripts/  WIZARD_UIFILES/
    build/gopeed-<version>-<arch>.spk

package.tgz ends up in /var/packages/gopeed/target on the NAS; the lifecycle
scripts in scripts/ are executed by Package Center.

No Synology toolchain is required: an .spk is a plain tar.gz with a fixed
layout. Signing is not used (DSM 7 does not require it, and unsigned root
packages would be rejected anyway - this package runs as the package user).
"""
import hashlib
import os
import shutil
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
OUT = os.path.join(HERE, "build")

# Upstream binaries: ./dist when the repo is self-contained, else ../dist
# (the layout used while developing next to the gopeed source tree).
DIST = os.environ.get("GOPEED_DIST_DIR") or (
    os.path.join(HERE, "dist")
    if os.path.isdir(os.path.join(HERE, "dist"))
    else os.path.abspath(os.path.join(HERE, "..", "dist"))
)

UPSTREAM_VERSION = "1.9.3"
PKG_VERSION = "1.9.3-17"
CHANGELOG = "1.9.3-17 same-origin web UI inside the DSM window (CORS tunnel)"

# DSM arch value -> goarch used by the upstream release assets
ARCHES = {
    "x86_64": "amd64",
    "armv8": "arm64",
}

DEFAULT_PORT = "38099"

# files that must keep the executable bit inside the tar archives
def is_exec(arcname):
    return (
        arcname.startswith("scripts/")
        or arcname in ("bin/gopeed", "bin/gopeed-tunnel.py")
        or arcname.endswith(".sh")
    )


def mode_filter(ti):
    # never ship python bytecode caches
    if "__pycache__" in ti.name or ti.name.endswith(".pyc"):
        return None
    ti.mode = 0o755 if (ti.isdir() or is_exec(ti.name)) else 0o644
    ti.uid = 0
    ti.gid = 0
    ti.uname = ""
    ti.gname = ""
    return ti


def binary_for(goarch):
    path = os.path.join(DIST, goarch, "gopeed-web-v%s-linux-%s" % (UPSTREAM_VERSION, goarch), "gopeed")
    if not os.path.isfile(path):
        sys.exit("missing upstream binary: %s" % path)
    return path


def replace_in(path, pairs):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for old, new in pairs:
        text = text.replace(old, new)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def replace_tree(root, pairs, skip=("package/bin",)):
    """Substitute placeholders in every text file below root (skip the payload,
    which contains the binary). Cheap and impossible to forget a new script."""
    skip_paths = {os.path.join(root, *name.split("/")) for name in skip}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if os.path.join(dirpath, d) not in skip_paths]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue        # binary or unreadable
            new = text
            for old, value in pairs:
                new = new.replace(old, value)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new)


def md5_of(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_one(arch, goarch, port):
    dst = os.path.join(OUT, "gopeed-%s" % arch)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst)

    payload = os.path.join(dst, "package")
    os.makedirs(os.path.join(payload, "bin"), exist_ok=True)
    shutil.copy2(binary_for(goarch), os.path.join(payload, "bin", "gopeed"))

    common = [("__VERSION__", PKG_VERSION), ("__PORT__", port)]
    replace_tree(dst, common + [
        ("__ARCH__", arch),
        ("__CHANGELOG__", CHANGELOG),
    ])

    # payload archive (paths are relative to /var/packages/gopeed/target)
    # GNU format on purpose: Python defaults to PAX, and DSM's package parser
    # rejects PAX extended headers with "invalid file format".
    tgz = os.path.join(dst, "package.tgz")
    with tarfile.open(tgz, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for entry in sorted(os.listdir(payload)):
            tar.add(os.path.join(payload, entry), arcname=entry, filter=mode_filter)

    replace_in(os.path.join(dst, "INFO"), [("__MD5__", md5_of(tgz))])

    # final .spk
    spk = os.path.join(OUT, "gopeed-%s-%s.spk" % (PKG_VERSION, arch))
    includes = [
        "INFO",
        "package.tgz",
        "LICENSE",
        "PACKAGE_ICON.PNG",
        "PACKAGE_ICON_256.PNG",
        "conf",
        "scripts",
        "WIZARD_UIFILES",
    ]
    # The .spk itself must be an *uncompressed* tar on DSM 7 - Package Center
    # rejects a gzip-wrapped one with "invalid file format". Only package.tgz
    # inside it is compressed.
    with tarfile.open(spk, "w", format=tarfile.GNU_FORMAT) as tar:
        for name in includes:
            full = os.path.join(dst, name)
            if not os.path.exists(full):
                sys.exit("missing package file: %s" % name)
            tar.add(full, arcname=name, filter=mode_filter)

    print("  %-10s -> %s (%.1f MB)" % (arch, os.path.basename(spk),
                                       os.path.getsize(spk) / 1024.0 / 1024.0))
    return spk


def main():
    port = DEFAULT_PORT
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--port":
        port = args[1]
        if not port.isdigit() or not (1024 <= int(port) <= 65535):
            sys.exit("--port must be 1024-65535")

    os.makedirs(OUT, exist_ok=True)
    print("building gopeed %s for DSM 7 (admin port %s)" % (PKG_VERSION, port))
    for arch, goarch in ARCHES.items():
        build_one(arch, goarch, port)


if __name__ == "__main__":
    main()
