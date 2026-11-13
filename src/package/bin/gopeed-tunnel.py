#!/usr/bin/env python3
"""HTTP/HTTPS auto-detect tunnel for the Synology Gopeed package.

Why: the DSM desktop iframe inherits the DSM page's protocol. Gopeed only
speaks HTTP, so an https DSM page cannot embed it (mixed content), and an
http:// iframe would be blocked too. Serving both protocols on the same port
(peek the first byte: 0x16 = TLS) makes http://nas:38099 and https://nas:38099
both work, with Gopeed listening on loopback behind it.

The certificate is whatever DSM uses as its default certificate. If it is
self-signed, browsers refuse to load it inside an iframe - in that case the
DSM desktop page shows an explanation instead (see ui/Gopeed.js).
"""
import os
import socket
import ssl
import sys
import threading

CHUNK = 65536
IDLE_TIMEOUT = 600


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_context(cert_file, key_file):
    if not (os.path.isfile(cert_file) and os.path.isfile(key_file)):
        log("tunnel: no certificate, http only")
        return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_file, key_file)
        return ctx
    except Exception as exc:
        log("tunnel: cannot load certificate (%s), http only" % exc)
        return None


def pump(src, dst, on_close):
    try:
        while True:
            data = src.recv(CHUNK)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        on_close()


def handle(conn, ctx, upstream):
    up = None
    try:
        conn.settimeout(IDLE_TIMEOUT)
        first = conn.recv(1, socket.MSG_PEEK)
        if not first:
            return
        if first == b"\x16":
            if ctx is None:
                log("tunnel: tls connection but no certificate")
                return
            try:
                conn = ctx.wrap_socket(conn, server_side=True)
            except Exception as exc:
                log("tunnel: tls handshake failed (%s)" % exc)
                return

        up = socket.create_connection(upstream, timeout=15)
        up.settimeout(IDLE_TIMEOUT)
        closed = threading.Event()

        def close_both():
            for s in (up, conn):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        thread = threading.Thread(target=pump, args=(up, conn, close_both), daemon=True)
        thread.start()
        pump(conn, up, close_both)
        thread.join(5)
    except Exception as exc:
        log("tunnel: conn error (%s)" % exc)
    finally:
        for s in (up, conn):
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass


def bind_sockets(port):
    socks = []
    try:
        v4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        v4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        v4.bind(("0.0.0.0", port))
        v4.listen(128)
        socks.append(v4)
    except Exception as exc:
        log("tunnel: ipv4 bind failed (%s)" % exc)
    try:
        v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        v6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except Exception:
            pass
        v6.bind(("::", port))
        v6.listen(128)
        socks.append(v6)
    except Exception as exc:
        log("tunnel: ipv6 bind failed (%s)" % exc)
    return socks


def accept_loop(srv, ctx, upstream):
    while True:
        try:
            conn, _ = srv.accept()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            log("tunnel: accept error (%s)" % exc)
            continue
        threading.Thread(target=handle, args=(conn, ctx, upstream), daemon=True).start()


def main():
    port = int(os.environ.get("TUNNEL_PORT", "38099"))
    upstream_port = int(os.environ.get("UPSTREAM_PORT", str(port + 1)))
    cert = os.environ.get("CERT_FILE", "")
    key = os.environ.get("KEY_FILE", "")

    ctx = load_context(cert, key) if cert and key else None
    socks = bind_sockets(port)
    if not socks:
        log("tunnel: cannot bind port %d" % port)
        sys.exit(1)

    log("tunnel: listening on %d -> 127.0.0.1:%d (tls=%s)"
        % (port, upstream_port, "yes" if ctx else "no"))

    upstream = ("127.0.0.1", upstream_port)
    for idx, srv in enumerate(socks):
        if idx == len(socks) - 1:
            accept_loop(srv, ctx, upstream)
        else:
            threading.Thread(target=accept_loop, args=(srv, ctx, upstream), daemon=True).start()


if __name__ == "__main__":
    main()
