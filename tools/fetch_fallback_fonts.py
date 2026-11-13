#!/usr/bin/env python3
"""Mirror Flutter web's Noto fallback fonts into the offline web bundle.

Flutter web resolves a missing glyph by downloading the whole Noto family from
`<fontFallbackBaseUrl><family>/<version>/<hash>.<index>.woff2` (the base
defaults to `assets/gstatic/`). On a NAS without internet those requests fail
and every unsupported script renders as tofu boxes - which is what the language
picker showed for Persian/Korean/Tamil.

The URL table lives in main.dart.js, so parse it from the bundle itself instead
of hardcoding paths.

    python fetch_fallback_fonts.py <ui/web>                 # only what i18n needs
    python fetch_fallback_fonts.py <ui/web> --all           # every family
    python fetch_fallback_fonts.py <ui/web> --families "Noto Sans KR,Noto Sans Arabic"
    python fetch_fallback_fonts.py <ui/web> --list
"""
import argparse
import collections
import concurrent.futures
import os
import re
import sys
import urllib.request

CDN = "https://fonts.gstatic.com/s/"

# Families needed to render every string of the bundled UI translations and the
# native language names in the picker, on top of Noto Sans SC (already shipped,
# which also covers kana and the Latin/Greek/Cyrillic of Source Han Sans).
DEFAULT_FAMILIES = ["Noto Sans KR", "Noto Sans Arabic", "Noto Sans Tamil"]


def table(web):
    js = open(os.path.join(web, "main.dart.js"), encoding="utf-8", errors="replace").read()
    fams = collections.OrderedDict()
    for m in re.finditer(r'A\.f\("([^"]+)","([^"]+)"\)', js):
        label, path = m.group(1), m.group(2)
        fam = re.sub(r"\s+\d+$", "", label)
        fams.setdefault(fam, []).append(path)
    return fams


def fetch(job):
    fam, rel, dest = job
    if os.path.isfile(dest):
        return fam, rel, os.path.getsize(dest), "cached"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(CDN + rel, timeout=60) as r, open(tmp, "wb") as f:
            f.write(r.read())
        os.replace(tmp, dest)
        return fam, rel, os.path.getsize(dest), "ok"
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return fam, rel, 0, "FAIL %s" % exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("web")
    ap.add_argument("--families", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()

    web = a.web
    fams = table(web)

    if a.list:
        for fam, paths in fams.items():
            print("%-42s %3d file(s)" % (fam, len(paths)))
        print("\n%d families, %d files" % (len(fams), sum(len(v) for v in fams.values())))
        return 0

    if a.all:
        want = list(fams)
    elif a.families:
        want = [f.strip() for f in a.families.split(",") if f.strip()]
    else:
        want = DEFAULT_FAMILIES

    jobs, unknown = [], []
    for fam in want:
        if fam not in fams:
            unknown.append(fam)
            continue
        for rel in fams[fam]:
            jobs.append((fam, rel, os.path.join(web, "assets", "gstatic", *rel.split("/"))))
    for fam in unknown:
        print("warning: %r is not in the bundle's fallback table" % fam)

    if not jobs:
        print("nothing to do")
        return 1

    per_fam = collections.Counter()
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for fam, rel, size, state in pool.map(fetch, jobs):
            if state.startswith("FAIL"):
                failed += 1
                print("  %s: %s" % (state, rel))
                continue
            per_fam[fam] += size

    for fam in want:
        if fam in per_fam:
            print("%-24s %6.2f MB" % (fam, per_fam[fam] / 1048576.0))
    print("total %.2f MB in %d files, %d failed"
          % (sum(per_fam.values()) / 1048576.0, len(jobs), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
