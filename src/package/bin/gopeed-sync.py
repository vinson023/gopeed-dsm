#!/usr/bin/env python3
"""Push the DSM-side settings (download dir, BT / ED2K ports, maxRunning) into Gopeed.

It also keeps `protocolConfig.http` present. The Flutter settings page casts that
object to a Map without a null check, so when the engine omits it (it does when a
`-c` seed replaces protocolConfig, or after a config write that leaves it out) the
*entire* config fails to deserialise and the UI silently shows defaults: download
directory "/", maxRunning 0 and all three ports 0. New tasks then carry path "./"
and land in the engine's working directory instead of the configured folder.

Gopeed applies `downloadConfig` from -c only when its storage is initialised for
the very first time, so on every later start - and on any upgrade, where
/var/packages/gopeed/var survives - the engine keeps whatever it stored. The
install wizard's download dir, BT port and ED2K ports would then be ignored and
the settings page shows 0 for all three ports (0 = random port, unreachable
from outside).

Worse, the stored config can be rewritten to a default state *after* startup
(seen on a fresh install: maxRunning=0, downloadDir="./", all ports 0, only
extra.bt.* filled in by the tracker job). maxRunning 0 freezes the queue, so
every task sits in "wait" and nothing downloads. `--watch` keeps an eye on that
and restores the configured values when - and only when - the config looks
wiped, so a change made in the web UI is left alone.

Usage:
  gopeed-sync.py --port 38100 --dir /volume1/downloads \
                 [--bt-port 51413] [--ed2k-tcp 4662] [--ed2k-udp 4672] \
                 [--max-running 5] [--user admin --pass pw]
  gopeed-sync.py ... --heal               # only act when the config looks wiped
  gopeed-sync.py ... --watch 60           # heal pass every 60 s, forever

Exit codes:
  0 nothing to do / written and verified / healthy (with --heal)
  2 login failed
  3 read config failed
  4 write config failed
  5 written but the engine still reports different values
"""
import argparse
import json
import sys
import time
import urllib.request


def call(base, path, method="GET", payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def sane_port(value):
    """0 = leave whatever the engine has; anything invalid is treated as 0."""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return port if 0 < port <= 65535 else 0


def read_config(base, token):
    res = call(base, "/api/v1/config", "GET", token=token)
    return (res or {}).get("data") or {}


def ports_of(cfg):
    pc = cfg.get("protocolConfig") or {}
    bt = pc.get("bt") or {}
    ed = pc.get("ed2k") or {}
    return (int(bt.get("listenPort") or 0),
            int(ed.get("listenPort") or 0),
            int(ed.get("udpPort") or 0))


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36")


def ensure_http(cfg):
    """Make sure protocolConfig.http is a complete object; returns True if patched.

    The web UI parses `protocolConfig.http` (and proxy) with a plain
    `as Map<String, dynamic>` cast. A null there throws, the whole config falls
    back to its defaults, and every setting then reads 0 -- including the ports.
    It must therefore never be absent, even when nothing else changes.
    """
    proto = cfg.setdefault("protocolConfig", {})
    http = proto.get("http")
    changed = False
    if not isinstance(http, dict):
        http = {}
        proto["http"] = http
        changed = True
    for key, value in (("userAgent", DEFAULT_UA), ("connections", 16),
                       ("useServerCtime", False)):
        if key not in http or http[key] in (None, ""):
            http[key] = value
            changed = True
    if "proxy" not in cfg or cfg.get("proxy") is None:
        cfg["proxy"] = {"enable": False, "system": False, "scheme": "",
                        "host": "", "usr": "", "pwd": ""}
        changed = True
    return changed


def looks_wiped(cfg):
    bt, tcp, udp = ports_of(cfg)
    return (int(cfg.get("maxRunning") or 0) == 0
            or (cfg.get("downloadDir") or "") in ("", "./")
            or (bt == 0 and tcp == 0 and udp == 0))


def mismatches(cfg, want):
    bt, tcp, udp = ports_of(cfg)
    bad = []
    if want["dir"] and (cfg.get("downloadDir") or "").rstrip("/") != want["dir"]:
        bad.append("downloadDir")
    if want["bt"] and bt != want["bt"]:
        bad.append("bt")
    if want["tcp"] and tcp != want["tcp"]:
        bad.append("ed2k-tcp")
    if want["udp"] and udp != want["udp"]:
        bad.append("ed2k-udp")
    if want["max"] and int(cfg.get("maxRunning") or 0) != want["max"]:
        bad.append("maxRunning")
    return bad


def run_once(args, want, heal=False, quiet=False):
    base = "http://127.0.0.1:%d" % args.port
    token = None
    if args.password:
        try:
            res = call(base, "/api/web/login", "POST",
                       {"username": args.user, "password": args.password})
            token = (res or {}).get("data") or None
        except Exception as exc:
            print("login failed: %s" % exc)
            return 2

    try:
        cfg = read_config(base, token)
    except Exception as exc:
        print("read config failed: %s" % exc)
        return 3

    # Runs before the heal gate: a missing protocolConfig.http breaks the web UI
    # for *every* setting, and adding it never touches a value the user chose.
    http_patched = ensure_http(cfg)

    if heal and not looks_wiped(cfg) and not http_patched:
        if not quiet:
            print("config looks healthy, nothing to heal")
        return 0

    old_dir = (cfg.get("downloadDir") or "").rstrip("/")
    bt_old, tcp_old, udp_old = ports_of(cfg)
    max_old = int(cfg.get("maxRunning") or 0)

    changed = http_patched
    if http_patched:
        # always logged, even from the watchdog: this is the one repair that makes
        # the web UI usable again, so it must be visible in gopeed.log
        print("fixed: protocolConfig.http was missing - restored (the web UI "
              "cannot parse a config without it and shows 0 for everything)")
    # maxRunning 0 freezes the whole queue: every task sits in "wait" and the UI
    # looks broken. Gopeed's own default is 5.
    if want["max"] and max_old == 0:
        cfg["maxRunning"] = want["max"]
        changed = True
    if want["dir"] and old_dir != want["dir"]:
        cfg["downloadDir"] = want["dir"]
        changed = True

    # BitTorrent: 0 means "let the OS pick a random port", which no router rule
    # can be pointed at -- inbound peers and seeding then never work.
    if want["bt"] and bt_old != want["bt"]:
        proto = cfg.setdefault("protocolConfig", {})
        proto.setdefault("bt", {})["listenPort"] = want["bt"]
        changed = True

    # ED2K needs a fixed TCP+UDP port to be reachable (HighID).
    if (want["tcp"] and tcp_old != want["tcp"]) or (want["udp"] and udp_old != want["udp"]):
        proto = cfg.setdefault("protocolConfig", {})
        ed = proto.setdefault("ed2k", {})
        if want["tcp"] and tcp_old != want["tcp"]:
            ed["listenPort"] = want["tcp"]
        if want["udp"] and udp_old != want["udp"]:
            ed["udpPort"] = want["udp"]
        changed = True

    if changed:
        # Categories may carry absolute paths; keep them consistent with the new dir.
        for cat in cfg.get("categories") or []:
            path = (cat.get("path") or "").rstrip("/")
            if old_dir and path.startswith(old_dir + "/"):
                cat["path"] = want["dir"] + path[len(old_dir):]
            elif old_dir and path == old_dir:
                cat["path"] = want["dir"]
        try:
            call(base, "/api/v1/config", "PUT", cfg, token=token)
        except Exception as exc:
            print("write config failed: %s" % exc)
            return 4

    # Read back: a PUT that silently did nothing is exactly the failure mode
    # that made the settings page show 0 after a fresh install.
    try:
        cfg = read_config(base, token)
    except Exception as exc:
        print("verify failed: %s" % exc)
        return 3

    bad = mismatches(cfg, want)
    bt, tcp, udp = ports_of(cfg)
    print("engine: dir=%s bt=%d ed2k=%d/%d maxRunning=%d"
          % (cfg.get("downloadDir"), bt, tcp, udp, int(cfg.get("maxRunning") or 0)))
    if bad:
        print("MISMATCH after write: %s" % ", ".join(bad))
        return 5
    print("verified: %s" % ("written" if changed else "nothing to do"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--bt-port", default=0,
                    help="fixed BitTorrent listen port (0 keeps whatever is stored)")
    ap.add_argument("--ed2k-tcp", default=0, help="fixed ED2K TCP port (0 = leave as is)")
    ap.add_argument("--ed2k-udp", default=0, help="fixed ED2K UDP port (0 = leave as is)")
    ap.add_argument("--max-running", default=0,
                    help="restore maxRunning when it is 0 (0 = leave alone)")
    ap.add_argument("--heal", action="store_true",
                    help="only act when the stored config looks wiped")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="run a heal pass every N seconds, forever")
    ap.add_argument("--user", default="")
    ap.add_argument("--pass", dest="password", default="")
    args = ap.parse_args()

    want = {
        "dir": (args.dir or "").rstrip("/"),
        "bt": sane_port(args.bt_port),
        "tcp": sane_port(args.ed2k_tcp),
        "udp": sane_port(args.ed2k_udp),
        "max": int(args.max_running or 0),
    }

    if not args.watch:
        return run_once(args, want, heal=args.heal)

    while True:
        try:
            run_once(args, want, heal=True, quiet=True)
        except Exception as exc:                     # never let the watchdog die
            print("watch: %s" % exc)
        sys.stdout.flush()
        time.sleep(max(10, args.watch))


if __name__ == "__main__":
    sys.exit(main())
