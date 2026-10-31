"""Small, dependency-free research I/O. Never logs credentials or response bodies."""
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "CampaignPreservationAudit/1.0 (+https://github.com/anushreechaudhuri/ai-in-campaigns)"
MAX_BYTES = 16 * 1024 * 1024


class ReadFailure(ValueError):
    def __init__(self, message, bytes_read=0):
        super().__init__(message)
        self.bytes_read = bytes_read


def read_response(response, max_bytes):
    body = bytearray()
    read_size = 0
    try:
        while len(body) <= max_bytes:
            read_size = min(65536, max_bytes + 1 - len(body))
            chunk = (getattr(response, "read1", None) or response.read)(read_size)
            if not chunk:
                break
            body.extend(chunk)
    except Exception as error:
        partial = getattr(error, "partial", None)
        # A failed read can consume bytes without returning them. Charge its bounded
        # size conservatively when the transport cannot report a precise partial.
        consumed = len(partial) if isinstance(partial, bytes) else read_size
        raise ReadFailure("Response read failed: " + type(error).__name__, len(body) + consumed) from None
    if len(body) > max_bytes:
        raise ReadFailure("Response exceeds configured byte limit", len(body))
    return bytes(body)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def has_html_markup(body):
    pattern = rb'<(?:!doctype\s+html|html|head|body|main|article|div|p)[\s>]'
    if re.search(pattern, body[:65536], re.I):
        return True
    if body.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = body[:131072].decode('utf-16', 'replace')
        return bool(re.search(pattern.decode(), text, re.I))
    return False


def atomic_write(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".partial-", delete=False) as temporary:
        temp_path = Path(temporary.name)
        try:
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def public_target(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Only credential-free public HTTPS URLs on port 443 are accepted")
    if "\\" in p.path or re.search(r"%(?:2f|5c)", p.path, re.I) or any(segment in (".", "..") for segment in urllib.parse.unquote(p.path).split("/")):
        raise ValueError("Ambiguous source path requires manual review before robots evaluation")
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Non-public network target refused")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, "")), addresses


def public_url(url):
    return public_target(url)[0]


class VettedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the vetted socket address; verify TLS against the original host."""
    def __init__(self, hostname, addresses):
        super().__init__(hostname, port=443, timeout=25, context=ssl.create_default_context())
        self.addresses = addresses

    def connect(self):
        last_error = None
        for family, socktype, protocol, _, address in self.addresses:
            candidate = socket.socket(family, socktype, protocol)
            try:
                candidate.settimeout(self.timeout)
                candidate.connect(address)
                self.sock = self._context.wrap_socket(candidate, server_hostname=self.host)
                return
            except OSError as error:
                candidate.close()
                last_error = error
        raise last_error or OSError("No vetted address is available")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, headers=None, max_bytes=MAX_BYTES, method="GET", data=None, public=True):
    if public:
        url, addresses = public_target(url)
        parsed = urllib.parse.urlsplit(url)
        proxies = urllib.request.getproxies()
        if (proxies.get("https") or proxies.get("all")) and not urllib.request.proxy_bypass(parsed.hostname):
            # A generic CONNECT proxy chooses the origin address itself. Do not bypass
            # proxy policy or claim that local DNS validation constrains its connection.
            raise ValueError("Public capture requires vetted egress; configured HTTPS proxy cannot be bypassed")
        connection = VettedHTTPSConnection(parsed.hostname, addresses)
        try:
            target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request(method, target, body=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with connection.getresponse() as response:
                body = read_response(response, max_bytes)
                return response.status, dict(response.headers), body
        finally:
            connection.close()
    # Only fixed, trusted provider API URLs use this route, preserving configured
    # proxy handling. It is not exposed as an option to campaign/snapshot callers.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method, data=data)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=25)
    except urllib.error.HTTPError as error:
        # Preserve status/redirect location without leaking server bodies or bearer tokens.
        response = error
    with response:
        body = read_response(response, max_bytes)
        return response.status, dict(response.headers), body


def get_header(headers, key, default=""):
    return next((v for k, v in headers.items() if k.lower() == key.lower()), default)


def fetch_public(url, *, max_bytes=MAX_BYTES):
    for _ in range(6):
        status, headers, body = request(url, max_bytes=max_bytes)
        if status in (301, 302, 303, 307, 308):
            url = urllib.parse.urljoin(url, get_header(headers, "location"))
            continue
        if status != 200:
            raise ValueError(f"Public fetch returned HTTP {status}")
        return body, {"url": url, "retrieved_at": now(), "status": status,
                      "sha256": digest(body), "bytes": len(body),
                      "content_type": get_header(headers, "content-type"),
                      "etag": get_header(headers, "etag"),
                      "last_modified": get_header(headers, "last-modified")}
    raise ValueError("Too many redirects")


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Object path escapes corpus root")
    return path


def load_manifest(path):
    if not path:
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
