"""Small, dependency-free research I/O. Never logs credentials or response bodies."""
import hashlib
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "CampaignPreservationAudit/1.0 (+https://github.com/anushreechaudhuri/ai-in-campaigns)"
MAX_BYTES = 16 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def public_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Only credential-free public HTTPS URLs on port 443 are accepted")
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Non-public network target refused")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, headers=None, max_bytes=MAX_BYTES, method="GET", data=None, public=True):
    if public:
        url = public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method, data=data)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=25)
    except urllib.error.HTTPError as error:
        # Preserve status/redirect location without leaking server bodies or bearer tokens.
        response = error
    with response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Response exceeds configured byte limit")
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
