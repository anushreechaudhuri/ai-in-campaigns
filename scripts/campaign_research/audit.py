"""Coverage is evidence of bytes, never an inference from a detector score."""
import csv
import json
import re
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path
from .common import digest, has_html_markup, inside, load_manifest, now, read_json, write_json

ALIASES = {
    "candidate_id": ["candidate_id", "candidate_url", "ballotpedia_url", "Candidate URL", "Ballotpedia Candidate URL", "candidateUrl", "ballotpedia"],
    "name": ["name", "Candidate", "Candidate Name", "Name"],
    "website": ["website", "campaign_website", "Campaign Website", "Campaign website", "Website"],
    "status": ["status", "Status", "General election"],
    "primary": ["primary", "primary_status", "Primary", "Primary Result", "Primary result", "Primary Election Result"],
    "state": ["state", "State"], "chamber": ["chamber", "Chamber"],
    "district": ["district", "District"], "dashboard_id": ["dashboard_id"],
    "status_as_of": ["status_as_of", "Status as of"],
}


def pick(row, field):
    return next((str(row[k]).strip() for k in ALIASES[field] if row.get(k) is not None and str(row[k]).strip()), "")


def key(value):
    return value.strip().rstrip("/")


def identity(row):
    values = [re.sub(r"\s+", " ", str(row.get(k, "")).strip().casefold()) for k in ("name", "state", "chamber", "district")]
    return tuple(values) if all(values[:3]) else None


def inventory(path, dataset):
    if path:
        if Path(path).suffix.lower() == ".json":
            raw = read_json(path)
            raw = raw["candidates"] if isinstance(raw, dict) else raw
        else:
            with open(path, encoding="utf-8-sig", newline="") as source:
                raw = list(csv.DictReader(source))
        rows = [{field: pick(row, field) for field in ALIASES} for row in raw]
        if any(not row["candidate_id"] for row in rows):
            raise ValueError("Every inventory row needs candidate_id / Candidate URL; use the documented column mapping")
    else:
        rows = [{**{field: pick(row, field) for field in ALIASES}, "candidate_id": str(row["id"]), "dashboard_id": str(row["id"])} for row in dataset.get("candidates", [])]
    ids = [key(row["candidate_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate identities in inventory; resolve before auditing")
    return rows


def build_audit(rows, dataset, manifests, corpus_root=None):
    indexed = defaultdict(list)
    for candidate in dataset.get("candidates", []):
        indexed[("id", str(candidate["id"]))].append(candidate)
        if pick(candidate, "candidate_id"):
            indexed[("canonical", key(pick(candidate, "candidate_id")))].append(candidate)
        if identity(candidate):
            indexed[("identity", identity(candidate))].append(candidate)
    evidence = defaultdict(list)
    for record in manifests:
        evidence[key(str(record.get("candidate_id", "")))].append(record)
    output = []
    for candidate in rows:
        cid = key(candidate["candidate_id"])
        match, join_method = None, "unmatched"
        for method, lookup in [("canonical", cid), ("id", candidate["dashboard_id"]), ("identity", identity(candidate))]:
            matches = indexed.get((method, lookup), []) if lookup else []
            if len(matches) == 1:
                match, join_method = matches[0], method
                break
        records = evidence[cid]
        # A canonical manifest may alternatively use an explicitly supplied dashboard ID.
        if candidate["dashboard_id"] and candidate["dashboard_id"] != cid:
            records = records + evidence[candidate["dashboard_id"]]
        checked, metadata, invalid = [], [], []
        for record in records:
            if record.get("status") != "captured" or record.get("kind") not in ("campaign_page", "campaign_pdf", "policy_pdf"):
                continue
            if record.get('source_scope_excluded'):
                invalid.append('reviewed_unrelated_response')
                continue
            if not record.get("captured_at") or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256", ""))) or not record.get("source_url"):
                invalid.append("incomplete_source_receipt")
                continue
            try:
                timestamp = datetime.fromisoformat(record["captured_at"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("Capture time must include timezone")
            except (ValueError, TypeError):
                invalid.append("invalid_capture_time")
                continue
            if corpus_root and record.get("path"):
                try:
                    body = inside(corpus_root, record["path"]).read_bytes()
                    if digest(body) != record["sha256"]:
                        invalid.append("checksum_mismatch")
                    elif (record["kind"] in ("campaign_pdf", "policy_pdf") and not body.startswith(b"%PDF-")) or not body or (record["kind"] == "campaign_page" and not has_html_markup(body)):
                        invalid.append("invalid_source_bytes")
                    else:
                        checked.append(record)
                except (OSError, ValueError):
                    invalid.append("object_unavailable")
            else:
                metadata.append(record)
        def distinct_sources(items):
            ordered = sorted(items, key=lambda r: datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00")))
            return list({(r["source_url"], r["sha256"], r["kind"]): r for r in ordered}.values())
        checked, metadata = distinct_sources(checked), distinct_sources(metadata)
        docs = (match or {}).get("documents", [])
        website = candidate["website"] or (match or {}).get("website", "")
        risky = any(word in (candidate["status"] + " " + candidate["primary"]).casefold() for word in ("not advancing", "lost", "did not make", "withdraw"))
        preservation = "verified_source_bytes" if checked else "manifest_only" if metadata else "unknown"
        if checked:
            action, priority = "review_policy_scope_and_link_completeness", 5
        elif metadata or invalid:
            action, priority = "verify_manifest_objects", 1 if risky else 3
        elif website:
            action, priority = "preserve_known_website", 1 if risky else 3
        else:
            action, priority = "enrich_campaign_website", 2 if risky else 4
        output.append({**candidate, "candidate_id": cid, "website": website,
                       "dashboard_id": str((match or {}).get("id", candidate["dashboard_id"])),
                       "join_method": join_method, "preservation": preservation,
                       "verified_campaign_pages": sum(r["kind"] == "campaign_page" for r in checked),
                       "verified_campaign_pdfs": sum(r["kind"] == "campaign_pdf" for r in checked),
                       "verified_policy_pdfs": sum(r["kind"] == "policy_pdf" for r in checked),
                       "manifest_only_sources": len(metadata), "evidence_errors": ";".join(invalid),
                       "latest_capture": max((r["captured_at"] for r in checked), default="", key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))),
                       "detector_documents": len(docs) if match is not None else None,
                       "wayback_pointer": match.get("archive", "") if match is not None else None,
                       "priority": priority, "priority_basis": "Supplied outcome snapshot; current election status not verified", "next_action": action})
    return output


def audit(args):
    dataset = read_json(args.dataset) if args.dataset else {"candidates": []}
    if not args.inventory and not args.dataset:
        raise ValueError("Supply an inventory or dataset; no denominator may be assumed")
    rows = inventory(args.inventory, dataset)
    manifests = [record for path in args.manifest for record in load_manifest(path)]
    output = build_audit(rows, dataset, manifests, args.corpus_root)
    summary = {"generated_at": now(), "scope": "intended_inventory" if args.inventory else "published_dataset_only",
               "candidate_count": len(output), "expected_candidates": args.expected_candidates,
               "published_dataset_supplied": bool(args.dataset),
               "published_dataset_candidates": len(dataset.get("candidates", [])) if args.dataset else None,
               "denominator_matches_expectation": len(output) == args.expected_candidates if args.expected_candidates else None,
               "website_populated": sum(bool(r["website"]) for r in output),
               "preservation": dict(Counter(r["preservation"] for r in output)),
               "join_methods": dict(Counter(r["join_method"] for r in output)),
               "actions": dict(Counter(r["next_action"] for r in output)),
               "manifest_records": len(manifests), "unmatched_manifest_candidate_ids": sorted({key(str(r.get("candidate_id", ""))) for r in manifests} - {r["candidate_id"] for r in rows} - {r["dashboard_id"] for r in rows if r["dashboard_id"]}),
               "inputs": [{"path": str(p), "sha256": digest(Path(p).read_bytes())} for p in [args.inventory, args.dataset, *args.manifest] if p],
               "limitations": ["Unknown means unverified, not missing. A site URL or Wayback pointer is not proof of preservation.",
                               "Verified bytes confirm individual captures, not a complete site or complete policy corpus.",
                               "Detector report PDFs never count as campaign policy PDFs. Classification does not prove authorship.",
                               "Outcome priorities use supplied snapshot statuses and must be refreshed before treating them as current.",
                               "No live campaign fetches or Wayback availability checks were performed by this audit."]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "summary.json", summary)
    write_json(out / "coverage.json", output)
    for filename, records in [("coverage.csv", output), ("priority-queue.csv", sorted(output, key=lambda r: (r["priority"], r["candidate_id"])) )]:
        if records:
            with (out / filename).open("w", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
    print(json.dumps({k: summary[k] for k in ("scope", "candidate_count", "website_populated", "preservation", "actions")}))
    if summary["denominator_matches_expectation"] is False:
        raise ValueError("Inventory denominator differs from expectation; outputs retained for review")
