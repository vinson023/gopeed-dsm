#!/usr/bin/env python3
"""Same-origin API proxy for the Gopeed DSM package.

Why this exists
---------------
DSM serves every package's `ui/` directory under its own origin
(`/webman/3rdparty/<pkg>/...`) and **executes .cgi files found there**. That is
exactly how the working third-party package on this NAS (arc-control) talks to
its backend: `/webman/3rdparty/arc-control/scripts/api.cgi`.

Doing the same means the iframe, its static assets and every REST call all use
`http(s)://<nas>:5000|5001` - one origin, no second port. That is why it works
over http *and* https with no certificate, no TLS tunnel and no CORS headers:
the browser never sees a cross-origin request.

DSM runs package CGIs as root, so this script drops to the package user first.

Routing
-------
    GET /webman/3rdparty/gopeed/api.cgi/api/v1/tasks?page=1
        -> PATH_INFO    = /api/v1/tasks
        -> QUERY_STRING = page=1
        -> http://127.0.0.1:38099/api/v1/tasks?page=1
"""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UPSTREAM = "http://127.0.0.1:__PORT__"

# request headers worth forwarding upstream
PASS_HEADERS = {
    "HTTP_AUTHORIZATION": "Authorization",
    "HTTP_X_API_TOKEN": "X-Api-Token",
    # The web bundle fetches cross-origin images and the extension store through
    # the engine's /api/v1/proxy, passing the real URL in this header. Dropping
    # it made every such request fail (500) - the store then rendered as an
    # empty "no matching extensions" list.
    "HTTP_X_TARGET_URI": "X-Target-Uri",
    "HTTP_ACCEPT": "Accept",
    "HTTP_ACCEPT_LANGUAGE": "Accept-Language",
    "HTTP_USER_AGENT": "User-Agent",
    "CONTENT_TYPE": "Content-Type",
}

# response headers nginx must recompute itself
DROP_RESPONSE_HEADERS = {"transfer-encoding", "connection", "content-length", "keep-alive"}

BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def drop_privileges():
    """DSM executes package CGIs as root; run as the package user instead."""
    if os.geteuid() != 0:
        return
    try:
        import pwd

        pw = pwd.getpwnam("gopeed")
        os.setgid(pw.pw_gid)
        os.setgroups([])
        os.setuid(pw.pw_uid)
    except Exception:
        pass


def fail(status, message):
    body = ('{"code":%d,"msg":"%s","data":null}' % (status, message.replace('"', "'"))).encode()
    out = sys.stdout.buffer
    out.write(("Status: %d\r\n" % status).encode())
    out.write(b"Content-Type: application/json\r\n")
    out.write(b"Content-Length: %d\r\n\r\n" % len(body))
    out.write(body)
    out.flush()


def main():
    drop_privileges()

    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    path = os.environ.get("PATH_INFO") or "/"
    query = os.environ.get("QUERY_STRING") or ""

    # only ever talk to the local engine
    if not path.startswith("/api/"):
        fail(404, "not found")
        return

    url = UPSTREAM + path
    if query:
        url += "?" + query

    try:
        length = int(os.environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = sys.stdin.buffer.read(length) if length > 0 else b""

    data = body if (method in BODY_METHODS and body) else None
    req = urllib.request.Request(url, data=data, method=method)
    for env_key, header in PASS_HEADERS.items():
        value = os.environ.get(env_key)
        if value:
            req.add_header(header, value)
    remote = os.environ.get("REMOTE_ADDR")
    if remote:
        req.add_header("X-Forwarded-For", remote)

    try:
        resp = urllib.request.urlopen(req, timeout=600)
        status, headers, payload = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        status, headers, payload = exc.code, exc.headers, exc.read()
    except Exception as exc:
        fail(502, "gopeed proxy: %s" % exc)
        return

    out = sys.stdout.buffer
    out.write(("Status: %d\r\n" % status).encode())
    if headers is not None:
        for name, value in headers.items():
            if name.lower() in DROP_RESPONSE_HEADERS:
                continue
            out.write(("%s: %s\r\n" % (name, value)).encode("latin-1", "replace"))
    out.write(b"Content-Length: %d\r\n\r\n" % len(payload))
    out.write(payload)
    out.flush()


if __name__ == "__main__":
    main()
