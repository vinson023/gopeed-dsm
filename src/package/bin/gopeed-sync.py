#!/usr/bin/env python3
"""Push the DSM-side settings (download dir, BT / ED2K ports) into Gopeed.

Gopeed applies `downloadConfig` from -c only when its storage is initialised for
the very first time, so on every later start - and on any upgrade, where
/var/packages/gopeed/var survives - the engine keeps whatever it stored. The
install wizard's download dir, BT port and ED2K ports would then be ignored and
the settings page shows 0 for all three ports (0 = random port, unreachable
from outside).

This script reads the live config through the local REST API, sets the wanted
values, writes the config back and then re-reads it to prove the values landed.

Usage:
  gopeed-sync.py --port 38100 --dir /volume1/downloads \
                 [--bt-port 51413] [--ed2k-tcp 4662] [--ed2k-udp 4672] \
                 [--user admin --pass pw]
  (omit --pass entirely when web authentication is disabled)

Exit codes:
  0 nothing to do / written and verified
  2 login failed
  3 read config failed
  4 write config failed
  5 written but the engine still reports different values
"""
import argparse
import json
import sys
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


def report(cfg, want_dir, want_bt, want_tcp, want_udp):
    bt, tcp, udp = ports_of(cfg)
    print("engine: dir=%s bt=%d ed2k=%d/%d" % (cfg.get("downloadDir"), bt, tcp, udp))
    bad = []
    if want_dir and (cfg.get("downloadDir") or "").rstrip("/") != want_dir:
        bad.append("downloadDir")
    if want_bt and bt != want_bt:
        bad.append("bt")
    if want_tcp and tcp != want_tcp:
        bad.append("ed2k-tcp")
    if want_udp and udp != want_udp:
        bad.append("ed2k-udp")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--bt-port", default=0,
                    help="fixed BitTorrent listen port (0 keeps whatever is stored)")
    ap.add_argument("--ed2k-tcp", default=0,
                    help="fixed ED2K TCP port (0 = leave as is)")
    ap.add_argument("--ed2k-udp", default=0,
                    help="fixed ED2K UDP port (0 = leave as is)")
    ap.add_argument("--user", default="")
    ap.add_argument("--pass", dest="password", default="")
    args = ap.parse_args()

    want_dir = (args.dir or "").rstrip("/")
    want_bt = sane_port(args.bt_port)
    want_tcp = sane_port(args.ed2k_tcp)
    want_udp = sane_port(args.ed2k_udp)

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

    old_dir = (cfg.get("downloadDir") or "").rstrip("/")
    bt_old, tcp_old, udp_old = ports_of(cfg)

    changed = False
    if want_dir and old_dir != want_dir:
        cfg["downloadDir"] = want_dir
        changed = True

    # BitTorrent: 0 means "let the OS pick a random port", which no router rule
    # can be pointed at -- inbound peers and seeding then never work.
    if want_bt and bt_old != want_bt:
        proto = cfg.setdefault("protocolConfig", {})
        proto.setdefault("bt", {})["listenPort"] = want_bt
        changed = True

    # ED2K needs a fixed TCP+UDP port to be reachable (HighID).
    if (want_tcp and tcp_old != want_tcp) or (want_udp and udp_old != want_udp):
        proto = cfg.setdefault("protocolConfig", {})
        ed = proto.setdefault("ed2k", {})
        if want_tcp and tcp_old != want_tcp:
            ed["listenPort"] = want_tcp
        if want_udp and udp_old != want_udp:
            ed["udpPort"] = want_udp
        changed = True

    if changed:
        # Categories may carry absolute paths; keep them consistent with the new dir.
        for cat in cfg.get("categories") or []:
            path = (cat.get("path") or "").rstrip("/")
            if old_dir and path.startswith(old_dir + "/"):
                cat["path"] = want_dir + path[len(old_dir):]
            elif old_dir and path == old_dir:
                cat["path"] = want_dir
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

    bad = report(cfg, want_dir, want_bt, want_tcp, want_udp)
    if bad:
        print("MISMATCH after write: %s" % ", ".join(bad))
        return 5
    print("verified: %s" % ("written" if changed else "nothing to do"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
