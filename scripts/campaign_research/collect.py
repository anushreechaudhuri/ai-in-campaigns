"""Checkpointed, bounded policy acquisition from an explicitly supplied public seed."""
import csv
import json
import re
import threading
import time
import urllib.parse
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from .audit import inventory
from .capture import Collector
from .common import MAX_BYTES, decode_html, digest, inside, now, read_json, request, write_json

POLICY = re.compile(r"\b(issues?|polic(?:y|ies)|priorities|platform|agenda|vision|positions?|plans?|stand(?:s|ing)?)\b", re.I)
EXCLUDE = re.compile(r"privacy[-_/ ]+policy|cookie|terms[-_/ ]+(?:of|and|conditions|service)|/(?:donate|volunteer|press|news|blog|events?)(?:/|$)", re.I)


class Anchors(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.links, self.href, self.words = [], None, []
        self.feed(decode_html(body))

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.words = []

    def handle_data(self, text):
        if self.href is not None:
            self.words.append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.words).strip()))
            self.href = None


def origin(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme, p.netloc.lower())


def campaign_url(value):
    if not value:
        return ""
    value = value.strip()
    if value.startswith("http://"):
        value = "https://" + value[7:]
    elif "://" not in value:
        value = "https://" + value
    return value


class Engine(Collector):
    def __init__(self, out, max_bytes, max_requests, transport=request, sleeper=time.sleep):
        self.gates = defaultdict(threading.RLock)
        # Keep policy-cache locks separate from transport locks. A normal robots
        # redirect may cross origins; holding a transport lock across that request
        # could deadlock two opposite redirects.
        self.policy_gates = defaultdict(threading.RLock)
        self.times = {}
        self.delays = {}
        self.failures = {}
        self.state_lock = threading.Lock()
        self.budget_changed = threading.Condition(self.state_lock)
        self.requests = self.downloaded = self.reserved = 0
        self.identity = None
        self.max_bytes, self.max_requests = max_bytes, max_requests
        self.exhausted = False
        self.upstream, self.sleeper = transport, sleeper
        super().__init__(out, delay=2, transport=self.gated_request, sleeper=sleeper)

    def gated_request(self, url, **kwargs):
        host = origin(url)
        with self.gates[host]:
            with self.budget_changed:
                while True:
                    if self.exhausted or self.requests >= self.max_requests or self.downloaded >= self.max_bytes:
                        self.exhausted = True
                        self.budget_changed.notify_all()
                        raise ValueError("Acquisition request/byte budget exhausted")
                    available = self.max_bytes - self.downloaded - self.reserved
                    if available > 0:
                        break
                    self.budget_changed.wait()
                self.requests += 1
                reservation = min(kwargs.get("max_bytes", MAX_BYTES) + 1, available)
                self.reserved += reservation
            self.sleeper(max(0, self.times.get(host, 0) + self.delays.get(host, 2) - time.monotonic()))
            self.times[host] = time.monotonic()
            used = 0
            try:
                result = self.upstream(url, **{**kwargs, "max_bytes": reservation - 1})
                used = len(result[2])
                return result
            except Exception as error:
                used = getattr(error, "bytes_read", 0)
                raise
            finally:
                with self.budget_changed:
                    self.reserved -= reservation
                    self.downloaded += used
                    self.exhausted |= self.downloaded >= self.max_bytes
                    self.budget_changed.notify_all()

    def allowed(self, url):
        host = origin(url)
        with self.policy_gates[host]:
            if host in self.failures:
                raise ValueError(self.failures[host])
            try:
                receipt = super().allowed(url)
                robot_origin = urllib.parse.urlunsplit((*urllib.parse.urlsplit(url)[:2], "", "", ""))
                self.delays[host] = max(2, self.robots[robot_origin][0].delay)
                return receipt
            except Exception as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                if reason != "robots.txt disallows capture":
                    self.failures[host] = reason
                raise ValueError(reason) from None

    def acquire(self, cid, url, kind, records, context="", allow_pdf=True, require_pdf=False):
        if origin(url) in self.failures:
            raise ValueError(self.failures[origin(url)])
        record, _ = self.capture({"candidate_id": cid, "source_url": url, "kind": kind, "allow_pdf": allow_pdf, "require_pdf": require_pdf})
        if kind == "reference_page":
            record["kind"] = kind
        record["policy_context"] = context
        body = inside(self.out, record["path"]).read_bytes()
        if digest(body) != record["sha256"]:
            raise ValueError("Post-capture checksum mismatch")
        record["verified_at"] = now()
        records.append(record)
        with self.state_lock:
            with (self.out / "captures.jsonl").open("a") as target:
                target.write(json.dumps(record) + "\n")
        return record, body


def process_candidate(engine, candidate, max_pages, max_pdfs, supplemental=None):
    cid = candidate["candidate_id"]
    state = {"candidate_id": cid, "name": candidate["name"], "seed_website": candidate["website"],
             "started_at": now(), "status": "pending", "records": [], "errors": [],
             "policy_pages": 0, "policy_pdf_candidates": 0, "scope_complete": False,
             "attempt": candidate.get("_attempt", 1), "input_config_identity": engine.identity,
             "unexpected_pdf_responses_skipped": 0}
    records = state["records"]
    site = candidate["website"] or (supplemental or {}).get(candidate["name"].casefold(), "")
    if site and not candidate["website"]:
        state["website_evidence"] = "Exact unique-name match in supplied published dataset"
    try:
        if engine.exhausted:
            state["status"] = "budget_exhausted"
            return state
        if not site:
            try:
                record, body = engine.acquire(cid, cid, "reference_page", records, "Campaign website discovery from canonical candidate page", allow_pdf=False)
                for href, label in Anchors(body).links:
                    if re.search(r"(?:official\s+)?campaign\s+(?:web)?site|campaign\s+website", label, re.I):
                        proposed = urllib.parse.urljoin(record["final_url"], href)
                        if urllib.parse.urlsplit(proposed).hostname not in (None, "ballotpedia.org", "www.ballotpedia.org"):
                            site = proposed
                            state["website_evidence"] = {"source_url": record["final_url"], "sha256": record["sha256"], "anchor_text": label}
                            break
                if not site:
                    state["status"] = "missing_site"
                    return state
            except Exception as error:
                state["status"] = "discovery_failed"
                state["errors"].append(str(error) if isinstance(error, ValueError) else type(error).__name__)
                return state
        site = campaign_url(site)
        state["resolved_website"] = site
        policy_start = bool(POLICY.search(urllib.parse.urlsplit(site).path) and not EXCLUDE.search(urllib.parse.urlsplit(site).path))
        queue, seen, pdfs = [(site, policy_start, "Explicit candidate-linked campaign/policy URL")], set(), []
        pages = page_attempts = 0
        while queue and page_attempts < max_pages and not engine.exhausted:
            url, is_policy, context = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            page_attempts += 1
            try:
                record, body = engine.acquire(cid, url, "campaign_page", records, context, allow_pdf=state["policy_pdf_candidates"] < max_pdfs)
            except Exception as error:
                if isinstance(error, ValueError) and str(error).startswith("PDF response exceeds candidate PDF cap"):
                    state["unexpected_pdf_responses_skipped"] += 1
                state["errors"].append({"url": url, "reason": str(error) if isinstance(error, ValueError) else type(error).__name__})
                continue
            if record["kind"] != "campaign_page":
                state["policy_pdf_candidates"] += 1
                continue
            pages += 1
            if is_policy:
                state["policy_pages"] += 1
            for href, label in Anchors(body).links:
                target = urllib.parse.urljoin(record["final_url"], href).split("#", 1)[0]
                parsed = urllib.parse.urlsplit(target)
                if parsed.scheme != "https" or EXCLUDE.search(label + " " + parsed.path):
                    continue
                if parsed.path.lower().endswith(".pdf"):
                    if is_policy or POLICY.search(label + " " + parsed.path):
                        pdfs.append((target, record["final_url"]))
                elif origin(target) == origin(record["final_url"]) and POLICY.search(label + " " + parsed.path) and target not in seen:
                    queue.append((target, True, "Policy/issues link from " + record["final_url"]))
        for url, parent in list(dict(pdfs).items())[:max_pdfs - state["policy_pdf_candidates"]]:
            if engine.exhausted:
                break
            if url in seen:
                continue
            seen.add(url)
            try:
                engine.acquire(cid, url, "campaign_pdf", records, "Unreviewed PDF linked from policy context " + parent, require_pdf=True)
                state["policy_pdf_candidates"] += 1
            except Exception as error:
                state["errors"].append({"url": url, "reason": str(error) if isinstance(error, ValueError) else type(error).__name__})
        state["unvisited_policy_links"] = len({url for url, _, _ in queue if url not in seen})
        campaign_records = [r for r in records if r["kind"] != "reference_page"]
        if engine.exhausted:
            state["status"] = "budget_exhausted"
        elif not campaign_records:
            state["status"] = "capture_failed"
        elif state["errors"] or state["unvisited_policy_links"]:
            state["status"] = "partial"
        elif state["policy_pages"] or state["policy_pdf_candidates"]:
            state["status"] = "success"
        else:
            state["status"] = "no_policy_found_in_bounded_scan"
        return state
    finally:
        state["finished_at"] = now()
        write_json(engine.out / "checkpoints" / (digest(cid.encode()) + ".json"), state)


def collect(args):
    if not 1 <= args.workers <= 4 or not 1 <= args.max_pages <= 12 or not 0 <= args.max_pdfs <= 10:
        raise ValueError("Use 1-4 workers, 1-12 pages and 0-10 PDF candidates per campaign")
    if not 1 <= args.max_requests <= 100000 or not 1 <= args.max_mib <= 8192:
        raise ValueError("Use at most 100000 requests and 8192 MiB per run")
    if args.limit is not None and args.limit < 1:
        raise ValueError("Candidate limit must be positive")
    rows = inventory(args.inventory, {})
    if args.limit:
        rows = rows[:args.limit]
    out = Path(args.out)
    (out / "checkpoints").mkdir(parents=True, exist_ok=True)
    engine = Engine(out, args.max_mib * 1024 * 1024, args.max_requests)
    supplemental = {}
    if args.dataset:
        counts = Counter(r["name"].casefold() for r in rows)
        data = read_json(args.dataset).get("candidates", [])
        published_counts = Counter(r.get("name", "").casefold() for r in data)
        supplemental = {r["name"].casefold(): r["website"] for r in data if r.get("website") and counts[r["name"].casefold()] == published_counts[r["name"].casefold()] == 1}
    config = {"input_sha256": digest(Path(args.inventory).read_bytes()),
               "candidate_count": len(rows), "workers": args.workers, "max_pages": args.max_pages,
               "max_pdfs": args.max_pdfs, "max_mib": args.max_mib, "max_requests": args.max_requests,
               "dataset_sha256": digest(Path(args.dataset).read_bytes()) if args.dataset else None,
               "code_sha256": {p.name: digest(p.read_bytes()) for p in Path(__file__).parent.glob("*.py")}}
    engine.identity = digest(json.dumps(config, sort_keys=True).encode())
    config["identity"] = engine.identity
    config_path = out / "run-config.json"
    if config_path.exists():
        if read_json(config_path).get("identity") != engine.identity:
            raise ValueError("Existing output has different input/config/code; use a fresh output directory")
    else:
        write_json(config_path, config)
    invocation = {"id": uuid.uuid4().hex, "started_at": now(), "config_identity": engine.identity,
                  "budget_scope": "This invocation only; previous invocation receipts are retained",
                  "limitations": "A bounded scan and PDF link heuristic do not prove complete policy coverage. No detector requests."}
    invocation_path = out / "invocations" / (invocation["id"] + ".json")
    write_json(invocation_path, invocation)
    results = {}
    pending = []
    for row in rows:
        checkpoint = out / "checkpoints" / (digest(row["candidate_id"].encode()) + ".json")
        if checkpoint.exists():
            previous = read_json(checkpoint)
            if previous.get("input_config_identity") != engine.identity:
                raise ValueError("Checkpoint identity disagrees with immutable run config")
            if args.retry_failures and previous["status"] in ("discovery_failed", "capture_failed", "budget_exhausted", "partial") and previous.get("attempt", 1) < 2:
                pending.append({**row, "_attempt": previous.get("attempt", 1) + 1})
            else:
                results[row["candidate_id"]] = previous
        else:
            pending.append(row)
    def save():
        summary = {"updated_at": now(), "intended": len(rows), "completed": len(results),
                   "counts": dict(Counter(r["status"] for r in results.values())),
                   "network_requests": engine.requests, "response_body_budget_bytes_charged": engine.downloaded,
                   "budget_exhausted": engine.exhausted, "scoring_requests": 0,
                   "invocation_id": invocation["id"], "budget_scope": "current invocation response bodies; failed reads charged conservatively"}
        write_json(out / "summary.json", summary)
        write_json(invocation_path, {**invocation, "latest_summary": summary})
        print(json.dumps(summary), flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_candidate, engine, row, args.max_pages, args.max_pdfs, supplemental): row for row in pending}
        for future in as_completed(futures):
            row = futures[future]
            results[row["candidate_id"]] = future.result()
            if len(results) % 100 == 0:
                save()
    save()
    write_json(out / "coverage.json", sorted(results.values(), key=lambda r: r["candidate_id"]))
    with (out / "coverage.csv").open("w", newline="") as target:
        fields = ["candidate_id", "name", "status", "resolved_website", "policy_pages", "policy_pdf_candidates", "finished_at"]
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(results.values(), key=lambda r: r["candidate_id"]))
