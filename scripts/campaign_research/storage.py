"""Explicit file exchange with the existing R2 bucket. No credentials are persisted."""
import os
import json
import urllib.parse
from pathlib import Path
from .common import request, write_json, digest, now, get_header


def transfer(args):
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "e84fe353569d25772e1018a57e090b26")
    if not token:
        raise ValueError("CLOUDFLARE_API_TOKEN is required for explicit R2 exchange")
    if not args.key.startswith("private/") or any(p in ("", ".", "..") for p in args.key.split("/")):
        raise ValueError("Research exchange is restricted to explicit private/ object keys")
    url = ("https://api.cloudflare.com/client/v4/accounts/" + urllib.parse.quote(account, safe="") +
           "/r2/buckets/ai-in-campaigns-data/objects/" + urllib.parse.quote(args.key, safe="/"))
    headers = {"Authorization": "Bearer " + token}
    if args.direction == "pull":
        status, response_headers, body = request(url, headers=headers)
        if status != 200:
            raise ValueError(f"R2 download HTTP {status}")
        Path(args.file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.file).write_bytes(body)
        write_json(str(args.file) + ".r2-receipt.json", {"object_key": args.key, "retrieved_at": now(),
                                                      "sha256": digest(body), "bytes": len(body),
                                                      "etag": get_header(response_headers, "etag"),
                                                      "last_modified": get_header(response_headers, "last-modified")})
    else:
        body = Path(args.file).read_bytes()
        if len(body) > 16 * 1024 * 1024:
            raise ValueError("Upload exceeds bounded 16 MiB file limit")
        headers["Content-Type"] = "application/octet-stream"
        status, _, response_body = request(url, headers=headers, method="PUT", data=body)
        if status not in (200, 201):
            raise ValueError(f"R2 upload HTTP {status}")
        if json.loads(response_body).get("success") is not True:
            raise ValueError("R2 did not confirm upload success")
    print(f"R2 {args.direction} succeeded; bytes={len(body)} sha256={digest(body)}")


def upload_run(args):
    from types import SimpleNamespace
    if not args.key_prefix.startswith("private/cloud-runs/"):
        raise ValueError("Cloud runs must use private/cloud-runs/ prefixes")
    root = Path(args.root).resolve()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise ValueError("Run directory is empty")
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in files):
        raise ValueError("Run upload refuses symlinked or external files")
    # The completion manifest is uploaded last. A failed batch is never marked complete.
    receipts = [{"path": str(p.relative_to(root)), "sha256": digest(p.read_bytes()), "bytes": p.stat().st_size} for p in files if p.name != "run-manifest.json"]
    for receipt in receipts:
        transfer(SimpleNamespace(direction="push", key=args.key_prefix.rstrip("/") + "/" + receipt["path"], file=str(root / receipt["path"])))
    marker = root / "run-manifest.json"
    write_json(marker, {"upload_completed_at": now(), "key_prefix": args.key_prefix.rstrip("/"), "objects": receipts})
    transfer(SimpleNamespace(direction="push", key=args.key_prefix.rstrip("/") + "/run-manifest.json", file=str(marker)))
