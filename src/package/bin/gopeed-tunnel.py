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
import json
import os
import socket
import ssl
import sys
import time
import time
import threading
import urllib.parse

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

        # Deleting a task with "remove files" does not remove the in-flight
        # "<name>.part" of a single-file task. Collect what it owns *before*
        # the task disappears, clean up after a successful delete.
        guard = {"ids": [], "targets": []}
        if method.upper() == "DELETE":
            try:
                guard = delete_guard_targets(path, headers, upstream)
            except Exception as exc:
                log("delete guard: lookup failed (%s)" % exc)

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
        resp_status = 0
        while True:
            resp_head, resp_rest = recv_head(up)
            if not resp_head:
                return
            first = resp_head.split(b"\r\n", 1)[0]
            if first.startswith(b"HTTP/1.1 1") or first.startswith(b"HTTP/1.0 1"):
                continue
            try:
                resp_status = int(first.split(b" ")[1])
            except Exception:
                resp_status = 0
            break

        if origin:
            resp_head = inject_cors(resp_head, origin, req_headers)

        if not guard["ids"]:
            conn.sendall(resp_head + b"\r\n\r\n")
            if resp_rest:
                conn.sendall(resp_rest)
            while True:
                data = up.recv(CHUNK)
                if not data:
                    break
                conn.sendall(data)
            stop.set()
            return

        # A delete with files: buffer the (tiny) response so a panic that already
        # dropped the task record can be reported as success instead of
        # "runtime error: invalid memory address or nil pointer dereference".
        body = [resp_rest]
        while True:
            data = up.recv(CHUNK)
            if not data:
                break
            body.append(data)
        stop.set()
        payload = b"".join(body)

        deleted = 200 <= resp_status < 300
        if not deleted:
            try:
                deleted = not any(task_still_exists(upstream, tid, headers)
                                  for tid in guard["ids"])
            except Exception as exc:
                log("delete guard: verify failed (%s)" % exc)
                deleted = False

        if deleted and not (200 <= resp_status < 300):
            fix = b'{"code":0,"msg":"","data":null}'
            head = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    "Content-Length: %d\r\nConnection: close" % len(fix))
            resp_head = head.encode("latin-1")
            payload = fix
            log("delete guard: engine reported an error but the task is gone - "
                "answered 200")

        if origin:
            resp_head = inject_cors(resp_head, origin, req_headers)
        conn.sendall(resp_head if resp_head.endswith(b"\r\n\r\n")
                     else resp_head + b"\r\n\r\n")
        conn.sendall(payload)

        if guard["targets"]:
            if deleted:
                cleanup_partials(guard["targets"],
                                 ",".join(i[:8] for i in guard["ids"]))
            else:
                threading.Thread(target=delayed_cleanup,
                                 args=(upstream, guard, headers),
                                 daemon=True).start()
        return

        # Clean up only when the delete really happened. A 2xx is the normal
        # case; the engine also panics on some tasks ("invalid memory address")
        # *after* removing the task record but *before* removing the files, so a
        # non-2xx still counts as deleted when the task is gone - otherwise the
        # files would be orphaned forever.
        if guard["targets"]:
            deleted = 200 <= resp_status < 300
            if not deleted and guard["ids"]:
                try:
                    deleted = not any(task_still_exists(upstream, tid, headers)
                                      for tid in guard["ids"])
                except Exception as exc:
                    log("delete guard: verify failed (%s)" % exc)
                    deleted = False
            if deleted:
                cleanup_partials(guard["targets"],
                                 ",".join(i[:8] for i in guard["ids"]))
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


# ---------------------------------------------------------------- delete guard
#
# Gopeed writes in-flight data as "<name>.part" but its delete path (force=true)
# only removes the *final* name for single-file tasks; multi-file tasks go
# through RemoveAll and are fine. A partially downloaded torrent therefore keeps
# its .part file after "delete + delete files" - on a NAS that is easily tens of
# GB. The binary cannot be patched, so: before letting the delete through, ask
# the engine what the task owns, then remove "<file>.part" once the delete
# succeeded. Both routes are covered - DELETE /api/v1/tasks/<id> and the batch
# form DELETE /api/v1/tasks?id=..&id=.. that the web UI actually uses.

TASKS_PATH = "/api/v1/tasks"


def _oneshot(upstream, raw, timeout=20):
    s = socket.create_connection(upstream, timeout=timeout)
    s.settimeout(timeout)
    try:
        s.sendall(raw)
        out = []
        while True:
            try:
                data = s.recv(CHUNK)
            except socket.timeout:
                break
            if not data:
                break
            out.append(data)
        return b"".join(out)
    finally:
        try:
            s.close()
        except Exception:
            pass


def _split_response(resp):
    try:
        head, _, body = resp.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        status = int(lines[0].split(" ")[1])
        hdrs = {}
        for line in lines[1:]:
            key, _, value = line.partition(":")
            hdrs[key.strip().lower()] = value.strip()
        if "chunked" in hdrs.get("transfer-encoding", "").lower():
            buf, pos, dec = body, 0, []
            while True:
                eol = buf.index(b"\r\n", pos)
                size = int(buf[pos:eol].split(b";")[0], 16)
                if size == 0:
                    break
                dec.append(buf[eol + 2:eol + 2 + size])
                pos = eol + 2 + size + 2
            body = b"".join(dec)
        else:
            length = hdrs.get("content-length")
            if length and length.isdigit():
                body = body[:int(length)]
        return status, body
    except Exception:
        return 0, b""


def _fetch_task(upstream, task_id, headers):
    lines = ["GET %s/%s HTTP/1.1" % (TASKS_PATH, task_id),
             "Host: 127.0.0.1", "Connection: close"]
    # `headers` is the parsed request header list, not a dict
    for key in ("authorization", "cookie", "x-api-token"):
        value = get_header(headers, key)
        if value:
            lines.append("%s: %s" % (key.title(), value))
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
    status, body = 0, b""
    for attempt in range(2):
        try:
            status, body = _split_response(_oneshot(upstream, raw))
        except Exception:
            status, body = 0, b""
        if status == 200:
            break
        if attempt == 0:
            time.sleep(0.2)
    if status != 200:
        return None
    try:
        data = (json.loads(body.decode("utf-8", "replace")).get("data")) or None
    except Exception:
        return None
    if isinstance(data, list):                 # be tolerant of API shape changes
        for item in data:
            if isinstance(item, dict) and item.get("id") == task_id:
                return item
        return data[0] if data and isinstance(data[0], dict) else None
    return data


def _partial_targets(task):
    """Files the engine leaves behind for a single-file task (final + .part)."""
    if isinstance(task, list):
        task = task[0] if task and isinstance(task[0], dict) else {}
    if not isinstance(task, dict):
        return []
    meta = task.get("meta") or task
    if not isinstance(meta, dict):
        return []
    res = meta.get("res") or {}
    opts = meta.get("opts") or {}
    if res.get("name"):
        return []                      # folder task: RemoveAll covers it
    files = res.get("files") or []
    base = (opts.get("path") or "").rstrip("/")
    if not base or not os.path.isabs(base):
        return []                      # "./" and friends: nothing safe to do
    # A magnet that has not resolved its metadata yet has no Res.Files, but the
    # task already carries the display name - use it, otherwise deleting in that
    # window would leave the partial behind.
    first = files[0] if files else {}
    name = str(opts.get("name") or first.get("name") or task.get("name") or "").strip()
    if not name or name in (".", "..") or "/" in name:
        return []
    sub = str(first.get("path") or "").strip("/")
    if sub in ("", "."):
        target = os.path.join(base, name)
    else:
        if ".." in sub.split("/"):
            return []
        target = os.path.join(base, sub, name)
    try:
        root = os.path.abspath(base)
        real = os.path.abspath(target)
        if os.path.commonpath([root, real]) != root or real == root:
            return []
    except Exception:
        return []
    return [real + ".part", real]


def delete_guard_targets(path, headers, upstream):
    """{"ids": [...], "targets": [...]} for a force=true DELETE, else empty."""
    empty = {"ids": [], "targets": []}
    if not path.startswith(TASKS_PATH):
        return empty
    raw_path, _, query = path.partition("?")
    params = urllib.parse.parse_qs(query)
    if (params.get("force") or ["false"])[0].lower() != "true":
        return empty                   # "keep files" - leave everything alone

    ids = []
    rest = raw_path[len(TASKS_PATH):].strip("/")
    if rest:
        ids = [rest]
    else:
        for key in ("id", "ids"):
            ids.extend(params.get(key) or [])
    ids = [urllib.parse.unquote(i).strip() for i in ids if i][:50]
    ids = [i for i in ids if i and "/" not in i and i not in (".", "..")]

    targets = []
    for tid in ids:
        targets.extend(_partial_targets(_fetch_task(upstream, tid, headers)))
    return {"ids": ids, "targets": targets}


def task_still_exists(upstream, task_id, headers, retries=8, pause=0.5):
    """Ask (a few times) whether the task record survived.

    A panic in the engine happens *after* the record is dropped, so the GET that
    follows the failed DELETE can still see the task for a moment. Retrying
    avoids treating "just deleted, panic pending" as "delete failed".
    """
    for attempt in range(retries):
        task = _fetch_task(upstream, task_id, headers)
        if not task:
            return False
        if attempt + 1 < retries:
            time.sleep(pause)
    return True


def delayed_cleanup(upstream, guard, headers, delay=3.0, tries=3):
    """Second chance for deletes whose response looked like a failure.

    The engine panics *after* dropping the task record, and right after such a
    panic a follow-up GET may fail or still see the task. Instead of giving up we
    re-check in the background and clean up as soon as the record is really gone.
    """
    for _ in range(tries):
        time.sleep(delay)
        try:
            alive = [tid for tid in guard["ids"]
                     if task_still_exists(upstream, tid, headers, retries=1)]
        except Exception as exc:
            log("delete guard: recheck failed (%s)" % exc)
            continue
        if not alive:
            cleanup_partials(guard["targets"],
                             ",".join(i[:8] for i in guard["ids"]))
            return


def cleanup_partials(targets, task_hint=""):
    removed = []
    for path in targets:
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
                removed.append(path)
        except Exception as exc:
            log("delete guard: cannot remove %s (%s)" % (path, exc))
    if removed:
        log("delete guard%s: removed %s"
            % (" (" + task_hint + ")" if task_hint else "", ", ".join(removed)))


def _v4_socket(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)
    return s


def _v6_socket(port, v6only):
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
    s.bind(("::", port))
    s.listen(128)
    return s


def bind_sockets(port):
    """Listen on IPv4 *and* IPv6.

    Preferred shape is one `[::]` socket with IPV6_V6ONLY=0: a single listener
    that accepts both families, which is what `bindv6only=0` kernels give you.
    Some kernels refuse to clear V6ONLY, so the flag is read back and the split
    layout (`[::]` + `0.0.0.0`) is used only when it really is IPv6-only.
    Returns (sockets, description).
    """
    try:
        v6 = _v6_socket(port, 0)
        try:
            v6only = v6.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
        except OSError:
            v6only = 0
        if v6only == 0:
            return [v6], "ipv4+ipv6 (dual-stack)"
        # V6ONLY could not be cleared - keep IPv6, add a real IPv4 socket.
        socks = []
        try:
            socks.append(_v4_socket(port))
        except Exception as exc:
            log("tunnel: ipv4 bind failed (%s)" % exc)
        socks.append(v6)
        return socks, "ipv4+ipv6 (split)"
    except Exception as exc:
        log("tunnel: dual-stack bind failed (%s)" % exc)

    socks = []
    try:
        socks.append(_v4_socket(port))
    except Exception as exc:
        log("tunnel: ipv4 bind failed (%s)" % exc)
    try:
        socks.append(_v6_socket(port, 1))
    except Exception as exc:
        log("tunnel: ipv6 bind failed (%s)" % exc)
    if len(socks) == 2:
        return socks, "ipv4+ipv6 (split)"
    if socks:
        return socks, "ipv4 only"
    return [], "none"


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
    socks, families = bind_sockets(port)
    if not socks:
        log("tunnel: cannot bind port %d" % port)
        sys.exit(1)

    log("tunnel: listening on %d (%s) -> 127.0.0.1:%d (tls=%s, cors=on)"
        % (port, families, upstream_port, "yes" if ctx else "no"))

    upstream = ("127.0.0.1", upstream_port)
    for idx, srv in enumerate(socks):
        if idx == len(socks) - 1:
            accept_loop(srv, ctx, upstream)
        else:
            threading.Thread(target=accept_loop, args=(srv, ctx, upstream), daemon=True).start()


if __name__ == "__main__":
    main()
