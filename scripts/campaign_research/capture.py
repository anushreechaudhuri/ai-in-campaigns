"""Bounded page/PDF captures; no recursive crawl, scoring, or archive submissions."""
import csv
import json
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from .common import USER_AGENT, atomic_write, decode_html, digest, get_header, has_html_markup, now, public_url, request, write_json
from .robots import RobotsPolicy


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


class Collector:
    def __init__(self, out, delay=2, transport=request, sleeper=time.sleep):
        self.out = Path(out)
        self.delay = max(2, delay)
        self.transport = transport
        self.sleep = sleeper
        self.robots = {}

    def allowed(self, url):
        origin = urllib.parse.urlunsplit((*urllib.parse.urlsplit(url)[:2], "", "", ""))
        if origin not in self.robots:
            checked_at = now()
            robots_url, redirects = origin + "/robots.txt", []
            for hop in range(6):
                self.sleep(self.delay)
                status, headers, body = self.transport(robots_url, max_bytes=512 * 1024)
                if status not in (301, 302, 303, 307, 308):
                    break
                target = public_url(urllib.parse.urljoin(robots_url, get_header(headers, "location")))
                redirects.append({"from": robots_url, "to": target, "status": status,
                                  "checked_at": now(), "sha256": digest(body)})
                robots_url = target
            else:
                raise ValueError("robots.txt redirect limit; manual review required")
            receipt = {"url": origin + "/robots.txt", "checked_at": checked_at,
                       "final_url": robots_url, "redirects": redirects,
                       "status": status, "sha256": digest(body)}
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "robots").mkdir(exist_ok=True)
            robot_path = f"robots/{digest(body)}.txt"
            atomic_write(self.out / robot_path, body)
            receipt["path"] = robot_path
            if status == 404:
                parser = RobotsPolicy("", USER_AGENT)
            elif status == 200:
                if get_header(headers, 'content-encoding').lower() not in ('', 'identity'):
                    raise ValueError('robots.txt content encoding requires review; no rules assumed')
                try:
                    robot_text = body.decode('utf-8-sig')
                except UnicodeDecodeError:
                    raise ValueError('robots.txt is not UTF-8; manual review required') from None
                if "<html" in robot_text.lower() or "<!doctype html" in robot_text.lower():
                    raise ValueError("robots.txt returned HTML; manual review required")
                parser = RobotsPolicy(robot_text, USER_AGENT)
            else:
                # Auth, throttling, ambiguous responses, and errors fail closed.
                raise ValueError(f"robots.txt HTTP {status}; manual review required")
            self.robots[origin] = (parser, receipt)
        parser, receipt = self.robots[origin]
        if not parser.can_fetch(url):
            raise ValueError("robots.txt disallows capture")
        delay = max(parser.delay, self.delay)
        if delay > 30:
            raise ValueError("robots crawl delay exceeds this bounded job; manual review required")
        self.sleep(max(self.delay, delay))
        return receipt

    def capture(self, row):
        url = row["source_url"]
        checks, redirects = [], []
        for _ in range(6):
            url = public_url(url)
            checks.append(self.allowed(url))
            status, headers, body = self.transport(url)
            if status in (301, 302, 303, 307, 308):
                target = urllib.parse.urljoin(url, get_header(headers, "location"))
                redirects.append({"from": url, "to": target, "status": status})
                url = target
                continue
            if status != 200:
                raise ValueError(f"Source HTTP {status}; no capture accepted")
            content_type_header = get_header(headers, 'content-type')
            content_type = content_type_header.split(';')[0].strip().lower()
            content_encoding = get_header(headers, 'content-encoding').lower()
            if content_encoding not in ('', 'identity'):
                raise ValueError('Source content encoding requires review; identity representation expected')
            is_pdf = body.startswith(b"%PDF-")
            if is_pdf and row.get("allow_pdf") is False:
                raise ValueError("PDF response exceeds candidate PDF cap; sniffed body not retained")
            if not is_pdf and any(marker in decode_html(body).lower() for marker in ("/cdn-cgi/challenge-platform/", "cf-chl-", "<title>just a moment", "<title>access denied")):
                raise ValueError("Source returned an access-control page; manual review required")
            if not is_pdf and content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("Only HTML or PDF campaign sources accepted")
            if not is_pdf and not has_html_markup(body):
                raise ValueError('Source did not return recognizable HTML; no campaign content accepted')
            if not body or ((row.get("kind") == "policy_pdf" or row.get("require_pdf")) and not is_pdf):
                raise ValueError("Requested policy PDF did not return PDF bytes")
            sha = digest(body)
            relative = f"objects/{sha}.{'pdf' if is_pdf else 'html'}"
            (self.out / "objects").mkdir(parents=True, exist_ok=True)
            atomic_write(self.out / relative, body)
            captured_at = now()
            record = {"candidate_id": row["candidate_id"], "source_url": row["source_url"],
                      "final_url": url, "status": "captured", "captured_at": captured_at,
                      "kind": ("policy_pdf" if row.get("kind") == "policy_pdf" else "campaign_pdf") if is_pdf else "campaign_page", "path": relative,
                      "sha256": sha, "bytes": len(body), "content_type": content_type,
                      "content_type_header": content_type_header, 'content_encoding': content_encoding,
                      "etag": get_header(headers, "etag"), "last_modified": get_header(headers, "last-modified"),
                      "robots_checks": checks, "redirects": redirects,
                      "scope": "single_response_body; embedded assets not captured"}
            discovered = []
            if not is_pdf:
                parser = Links()
                parser.feed(decode_html(body))
                for href in parser.hrefs:
                    candidate = urllib.parse.urljoin(url, href)
                    p = urllib.parse.urlsplit(candidate)
                    if p.scheme == "https" and p.netloc == urllib.parse.urlsplit(url).netloc and p.path.lower().endswith(".pdf"):
                        discovered.append({"candidate_id": row["candidate_id"], "source_url": candidate.split("#")[0], "kind": "candidate_pdf", "discovered_from": url, "review_required": True})
            return record, discovered
        raise ValueError("Too many source redirects")


def capture(args):
    if not 1 <= args.limit <= 100:
        raise ValueError("Capture limit must be 1–100; submit bounded batches")
    with open(args.queue, encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    collector = Collector(args.out, args.delay)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    discoveries = []
    count = 0
    with (out / "captures.jsonl").open("a") as manifest:
        for row in rows:
            if count >= args.limit:
                break
            if row.get("next_action") not in (None, "", "preserve_known_website"):
                continue
            if str(row.get("review_required", "")).lower() in ("true", "1", "yes"):
                continue
            if not row.get("source_url"):
                row["source_url"] = row.get("website", "")
            if not row.get("source_url") or not row.get("candidate_id"):
                continue
            count += 1
            try:
                record, found = collector.capture(row)
                discoveries.extend(found)
            except Exception as error:
                # Network exception strings can contain credentials; log error types only.
                record = {"candidate_id": row["candidate_id"], "source_url": row["source_url"],
                          "checked_at": now(), "status": "blocked_or_error",
                          "reason": str(error) if isinstance(error, ValueError) else type(error).__name__}
            manifest.write(json.dumps(record) + "\n")
            manifest.flush()
    write_json(out / "discovered-pdfs.json", list({(r["candidate_id"], r["source_url"]): r for r in discoveries}.values()))
    write_json(out / "capture-summary.json", {"finished_at": now(), "attempted": count,
                                              "limit": args.limit, "scoring_requests": 0})
    print(json.dumps({"attempted": count, "limit": args.limit, "scoring_requests": 0}))
