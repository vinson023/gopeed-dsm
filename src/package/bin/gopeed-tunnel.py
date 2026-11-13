#!/usr/bin/env python3
"""HTTP/HTTPS auto-detect tunnel with CORS support for the Synology package.

Two jobs:

1. Serve http and https on the same port (peek the first byte: 0x16 = TLS).
   DSM's desktop iframe inherits the DSM page's protocol, so both must work.

2. Add CORS headers to the engine's responses. The DSM window embeds the
   bundle from /webman/3rdparty/gopeed/app/ (same origin as DSM, required by
   `frame-src 'self'`), while the REST engine listens on this port. Those calls
   are cross-origin, so the browser needs Access-Control-Allow-* headers -
   and preflight requests have to be answered here, because the upstream Go
   server knows nothing about CORS.

Gopeed's engine stays on loopback behind this tunnel.
"""
import os
import socket
import ssl
import sys
import threading

CHUNK = 65536
IDLE_TIMEOUT = 600
MAX_HEAD = 128 * 1024

CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"


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


def recv_head(sock, initial=b""):
    """Read up to and including the blank line; return (head, leftover)."""
    buf = initial
    while b"\r\n\r\n" not in buf:
        try:
            data = sock.recv(8192)
        except Exception:
            return b"", b""
        if not data:
            return b"", b""
        buf += data
        if len(buf) > MAX_HEAD:
            return b"", b""
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def parse_headers(head):
    lines = head.split(b"\r\n")
    if not lines:
        return None, []
    try:
        parts = lines[0].decode("latin-1").split(" ")
        if len(parts) < 3:
            return None, []
        start = (parts[0], parts[1], parts[2])
    except Exception:
        return None, []
    headers = []
    for line in lines[1:]:
        if b":" not in line:
            continue
        name, _, value = line.partition(b":")
        headers.append((name.decode("latin-1").strip(), value.decode("latin-1").strip()))
    return start, headers


def get_header(headers, name):
    low = name.lower()
    for k, v in headers:
        if k.lower() == low:
            return v
    return None


def cors_block(origin, req_headers):
    allow_headers = req_headers or "Authorization, Content-Type, X-Api-Token"
    return [
        "Access-Control-Allow-Origin: %s" % origin,
        "Access-Control-Allow-Credentials: true",
        "Access-Control-Allow-Methods: %s" % CORS_METHODS,
        "Access-Control-Allow-Headers: %s" % allow_headers,
        "Access-Control-Expose-Headers: Content-Disposition, Content-Length, Content-Type, X-Request-Id",
        "Access-Control-Max-Age: 86400",
        "Vary: Origin",
    ]


def reply_preflight(conn, origin, req_headers):
    lines = ["HTTP/1.1 204 No Content"]
    lines += cors_block(origin, req_headers)
    lines += ["Content-Length: 0", "Connection: close", "", ""]
    conn.sendall("\r\n".join(lines).encode("latin-1"))


def inject_cors(resp_head, origin, req_headers):
    """Insert CORS headers right after the status line."""
    try:
        text = resp_head.decode("latin-1")
    except Exception:
        return resp_head
    lines = text.split("\r\n")
    if not lines or not lines[0].startswith("HTTP/"):
        return resp_head
    # drop any CORS headers the upstream may already send
    keep = [lines[0]]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith("access-control-") or low.startswith("vary:"):
            continue
        keep.append(line)
    out = [keep[0]] + cors_block(origin, req_headers) + keep[1:]
    return "\r\n".join(out).encode("latin-1")


def proxy(conn, ctx, upstream):
    up = None
    tls = False
    try:
        conn.settimeout(IDLE_TIMEOUT)
        peek = conn.recv(1, socket.MSG_PEEK)
        if not peek:
            return
        if peek == b"\x16":
            if ctx is None:
                log("tunnel: tls connection but no certificate")
                return
            try:
                conn = ctx.wrap_socket(conn, server_side=True)
                tls = True
            except Exception as exc:
                log("tunnel: tls handshake failed (%s)" % exc)
                return

        req_head, leftover = recv_head(conn)
        if not req_head:
            return
        start, headers = parse_headers(req_head)
        if start is None:
            return
        method, path, version = start
        origin = get_header(headers, "Origin")
        req_headers = get_header(headers, "Access-Control-Request-Headers")

        # preflight: answer locally, never reach the engine
        if method.upper() == "OPTIONS" and get_header(headers, "Access-Control-Request-Method"):
            log("tunnel: preflight %s %s" % (method, path))
            reply_preflight(conn, origin or "*", req_headers)
            return

        up = socket.create_connection(upstream, timeout=15)
        up.settimeout(IDLE_TIMEOUT)

        # rebuild the request: force Connection: close so every response is a
        # single, parseable unit; drop Expect so the upstream never sends 100.
        out = ["%s %s %s" % (method, path, version)]
        for k, v in headers:
            low = k.lower()
            if low in ("connection", "expect", "proxy-connection"):
                continue
            out.append("%s: %s" % (k, v))
        out.append("Connection: close")
        up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
        if leftover:
            up.sendall(leftover)

        # forward the rest of the request body in the background
        stop = threading.Event()

        def push_body():
            try:
                while not stop.is_set():
                    data = conn.recv(CHUNK)
                    if not data:
                        break
                    up.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    up.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

        threading.Thread(target=push_body, daemon=True).start()

        # read the response head (skip any 1xx informational responses)
        while True:
            resp_head, resp_rest = recv_head(up)
            if not resp_head:
                return
            first = resp_head.split(b"\r\n", 1)[0]
            if first.startswith(b"HTTP/1.1 1") or first.startswith(b"HTTP/1.0 1"):
                continue
            break

        if origin:
            resp_head = inject_cors(resp_head, origin, req_headers)

        conn.sendall(resp_head + b"\r\n\r\n")
        if resp_rest:
            conn.sendall(resp_rest)

        while True:
            data = up.recv(CHUNK)
            if not data:
                break
            conn.sendall(data)
        stop.set()
    except Exception as exc:
        log("tunnel: conn error (%s)" % exc)
    finally:
        for s in (up, conn):
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass
        _ = tls


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
        threading.Thread(target=proxy, args=(conn, ctx, upstream), daemon=True).start()


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

    log("tunnel: listening on %d -> 127.0.0.1:%d (tls=%s, cors=on)"
        % (port, upstream_port, "yes" if ctx else "no"))

    upstream = ("127.0.0.1", upstream_port)
    for idx, srv in enumerate(socks):
        if idx == len(socks) - 1:
            accept_loop(srv, ctx, upstream)
        else:
            threading.Thread(target=accept_loop, args=(srv, ctx, upstream), daemon=True).start()


if __name__ == "__main__":
    main()
