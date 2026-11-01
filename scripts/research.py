#!/usr/bin/env python3
"""Cloud research entrypoint. Python 3.12+, standard library only."""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from campaign_research.audit import audit
from campaign_research.capture import capture
from campaign_research.collect import collect
from campaign_research.common import digest, fetch_public, now, read_json, write_json
from campaign_research.storage import transfer, upload_run


def snapshot(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "dataset.json").exists():
        raise ValueError("Snapshot output already contains a dataset; use a fresh run directory")
    receipts = []
    try:
        body, receipt = fetch_public(args.catalog)
        (out / "catalog.json").write_bytes(body)
        receipts.append({"role": "catalog", **receipt})
        catalog = json.loads(body)
        url = urllib.parse.urljoin(receipt["url"], catalog["dataset"])
        body, receipt = fetch_public(url)
        data = json.loads(body)
        if not isinstance(data.get("candidates"), list):
            raise ValueError("Published dataset has no candidates array")
        (out / "dataset.json").write_bytes(body)
        receipts.append({"role": "dataset", **receipt})
    except Exception as error:
        write_json(out / "snapshot-receipt.json", {"checked_at": now(), "status": "blocked_or_error", "receipts": receipts,
                                                   "reason": str(error) if isinstance(error, ValueError) else type(error).__name__})
        raise
    write_json(out / "snapshot-receipt.json", {"status": "downloaded", "receipts": receipts})
    print(json.dumps({"candidate_count": len(data["candidates"]), "dataset_sha256": digest(body)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("snapshot", help="Read public catalog and exact dataset, with receipt")
    s.add_argument("--catalog", default="https://midterms-ai.org/research-assets/catalog.json")
    s.add_argument("--out", required=True)
    s.set_defaults(run=snapshot)
    a = commands.add_parser("audit", help="Offline inventory/manifest reconciliation; never fetches campaigns")
    a.add_argument("--inventory", help="Canonical candidate CSV or JSON; private input")
    a.add_argument("--dataset", help="Existing public dashboard dataset JSON")
    a.add_argument("--manifest", action="append", default=[], help="Capture JSONL; repeat for multiple runs")
    a.add_argument("--corpus-root", help="Root for locally verified manifest paths")
    a.add_argument("--expected-candidates", type=int)
    a.add_argument("--out", required=True)
    a.set_defaults(run=audit)
    c = commands.add_parser("capture", help="Capture at most 100 queued public pages/PDFs, respecting robots")
    c.add_argument("--queue", required=True)
    c.add_argument("--limit", type=int, default=5)
    c.add_argument("--delay", type=float, default=2)
    c.add_argument("--out", required=True)
    c.set_defaults(run=capture)
    b = commands.add_parser("collect", help="Checkpointed full-seed campaign website discovery and bounded policy/PDF acquisition")
    b.add_argument("--inventory", required=True)
    b.add_argument("--dataset")
    b.add_argument("--limit", type=int)
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--max-pages", type=int, default=6)
    b.add_argument("--max-pdfs", type=int, default=4)
    b.add_argument("--max-mib", type=int, default=4096)
    b.add_argument("--max-requests", type=int, default=30000)
    b.add_argument("--retry-failures", action="store_true", help="Retry failed/partial checkpoints once; successful checkpoints are reused")
    b.add_argument("--out", required=True)
    b.set_defaults(run=collect)
    t = commands.add_parser("r2", help="Explicit private file transfer to/from existing bucket")
    t.add_argument("direction", choices=["pull", "push"])
    t.add_argument("--key", required=True)
    t.add_argument("--file", required=True)
    t.set_defaults(run=transfer)
    u = commands.add_parser("upload-run", help="Explicitly persist a completed run under private/cloud-runs/; writes to R2")
    u.add_argument("--root", required=True)
    u.add_argument("--key-prefix", required=True)
    u.set_defaults(run=upload_run)
    args = parser.parse_args()
    try:
        args.run(args)
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, KeyError)) else type(error).__name__
        print(f"Research command failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
