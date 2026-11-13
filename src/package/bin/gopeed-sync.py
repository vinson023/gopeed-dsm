#!/usr/bin/env python3
"""Push the DSM-side settings (download dir, BT / ED2K ports) into Gopeed.

Gopeed applies `downloadConfig` from -c only when its storage is initialised for
the very first time, so on every later start - and on any upgrade, where
/var/packages/gopeed/var survives - the engine keeps whatever it stored. The
install wizard's download dir, BT port and ED2K ports would then be ignored and
the settings page shows 0 for all three ports (0 = random port, unreachable
from outside).

This script reads the live config through the local REST API, sets the wanted
values and writes the config back. It is idempotent: nothing is written when
the running config already matches.

Usage:
  gopeed-sync.py --port 38100 --dir /volume1/downloads \
                 [--bt-port 51413] [--ed2k-tcp 4662] [--ed2k-udp 4672] \
                 [--user admin --pass pw]
  (omit --pass entirely when web authentication is disabled)

Exit codes: 0 ok / nothing to do, 2 login failed, 3 read failed, 4 write failed
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
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--bt-port", type=int, default=0,
                    help="fixed BitTorrent listen port (0 keeps Gopeed's random port)")
    ap.add_argument("--ed2k-tcp", type=int, default=0,
                    help="fixed ED2K TCP port (0 = random; eMule HighID needs a fixed one)")
    ap.add_argument("--ed2k-udp", type=int, default=0,
                    help="fixed ED2K UDP port (0 = random)")
    ap.add_argument("--user", default="")
    ap.add_argument("--pass", dest="password", default="")
    args = ap.parse_args()

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
        res = call(base, "/api/v1/config", "GET", token=token)
    except Exception as exc:
        print("read config failed: %s" % exc)
        return 3

    cfg = (res or {}).get("data") or {}
    old_dir = (cfg.get("downloadDir") or "").rstrip("/")
    want = args.dir.rstrip("/")

    changed = False
    if old_dir != want:
        cfg["downloadDir"] = want
        changed = True

    # BitTorrent: 0 means "let the OS pick a random port", which no router rule
    # can be pointed at -- inbound peers and seeding then never work.
    bt = (cfg.get("protocolConfig") or {}).get("bt") or {}
    bt_old = int(bt.get("listenPort") or 0)
    if args.bt_port and bt_old != args.bt_port:
        bt["listenPort"] = args.bt_port
        cfg.setdefault("protocolConfig", {})["bt"] = bt
        changed = True

    # ED2K needs a fixed TCP+UDP port to be reachable (HighID); random ports
    # leave you on LowID with long queues.
    ed = (cfg.get("protocolConfig") or {}).get("ed2k") or {}
    ed_tcp_old = int(ed.get("listenPort") or 0)
    ed_udp_old = int(ed.get("udpPort") or 0)
    ed_changed = False
    if args.ed2k_tcp and ed_tcp_old != args.ed2k_tcp:
        ed["listenPort"] = args.ed2k_tcp
        ed_changed = True
    if args.ed2k_udp and ed_udp_old != args.ed2k_udp:
        ed["udpPort"] = args.ed2k_udp
        ed_changed = True
    if ed_changed:
        cfg.setdefault("protocolConfig", {})["ed2k"] = ed
        changed = True

    if not changed:
        print("config already points at %s" % want)
        return 0

    # Categories may carry absolute paths; keep them consistent with the new dir.
    for cat in cfg.get("categories") or []:
        path = (cat.get("path") or "").rstrip("/")
        if old_dir and path.startswith(old_dir + "/"):
            cat["path"] = want + path[len(old_dir):]
        elif old_dir and path == old_dir:
            cat["path"] = want

    try:
        call(base, "/api/v1/config", "PUT", cfg, token=token)
    except Exception as exc:
        print("write config failed: %s" % exc)
        return 4

    if old_dir != want:
        print("download dir set: %s -> %s" % (old_dir or "(empty)", want))
    if args.bt_port and bt_old != args.bt_port:
        print("bt listen port set: %d -> %d" % (bt_old, args.bt_port))
    if args.ed2k_tcp and ed_tcp_old != args.ed2k_tcp:
        print("ed2k tcp port set: %d -> %d" % (ed_tcp_old, args.ed2k_tcp))
    if args.ed2k_udp and ed_udp_old != args.ed2k_udp:
        print("ed2k udp port set: %d -> %d" % (ed_udp_old, args.ed2k_udp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
