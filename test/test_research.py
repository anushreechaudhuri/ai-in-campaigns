"""Offline research contract tests; no campaign traffic or credentials required."""
import argparse
import csv
import json
import sys
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from campaign_research.audit import audit, build_audit, inventory
from campaign_research.capture import Collector
from campaign_research.common import digest, inside, public_url, public_target, VettedHTTPSConnection, request
from campaign_research.robots import RobotsPolicy
from campaign_research.storage import transfer, upload_run
from research import snapshot


def candidate(**extra):
    return dict(candidate_id="https://ballotpedia.org/Jane_Doe", name="Jane Doe", state="ME", chamber="House", district="1", website="https://jane.example", status="Not advancing", primary="Lost", dashboard_id="", **extra)


class AuditTests(unittest.TestCase):
    def test_reports_and_wayback_pointers_are_not_source_preservation(self):
        dataset = {"candidates": [{"id": "jane", "name": "Jane Doe", "state": "ME", "chamber": "House", "district": "1", "archive": "https://web.archive.org/web/123/example", "documents": [{"pdf": "/research-assets/report.pdf"}]}]}
        row = build_audit([candidate()], dataset, [dict(candidate_id=candidate()["candidate_id"], kind="detector_report", status="captured")])[0]
        self.assertEqual(row["preservation"], "unknown")
        self.assertEqual(row["detector_documents"], 1)
        self.assertEqual(row["verified_policy_pdfs"], 0)
        self.assertEqual(row["priority"], 1)
        self.assertEqual(row["join_method"], "identity")

    def test_source_hash_and_available_bytes_are_required(self):
        with tempfile.TemporaryDirectory() as root:
            body = b"%PDF-1.7\npolicy"
            Path(root, "policy.pdf").write_bytes(body)
            record = dict(candidate_id=candidate()["candidate_id"], kind="policy_pdf", status="captured", source_url="https://jane.example/policy.pdf", captured_at="2026-10-30T00:00:00+00:00", path="policy.pdf", sha256=digest(body))
            self.assertEqual(build_audit([candidate()], {}, [record])[0]["preservation"], "manifest_only")
            verified = build_audit([candidate()], {}, [record], root)[0]
            self.assertEqual(verified["verified_policy_pdfs"], 1)
            repeated = build_audit([candidate()], {}, [record, {**record, "captured_at": "2026-10-30T01:00:00+00:00"}], root)[0]
            self.assertEqual(repeated["verified_policy_pdfs"], 1)
            self.assertEqual(repeated["latest_capture"], "2026-10-30T01:00:00+00:00")
            unreviewed = build_audit([candidate()], {}, [{**record, "kind": "campaign_pdf"}], root)[0]
            self.assertEqual(unreviewed["preservation"], "verified_source_bytes")
            self.assertEqual(unreviewed["verified_campaign_pdfs"], 1)
            self.assertEqual(unreviewed["verified_policy_pdfs"], 0)
            Path(root, "policy.pdf").write_bytes(b"altered")
            bad = build_audit([candidate()], {}, [record], root)[0]
            self.assertEqual(bad["preservation"], "unknown")
            self.assertEqual(bad["evidence_errors"], "checksum_mismatch")

    def test_ambiguous_identity_is_never_joined(self):
        duplicate = dict(name="Jane Doe", state="ME", chamber="House", district="1", documents=[{}])
        rows = build_audit([candidate()], {"candidates": [{"id": "a", **duplicate}, {"id": "b", **duplicate}]}, [])
        self.assertEqual(rows[0]["join_method"], "unmatched")
        self.assertIsNone(rows[0]["detector_documents"])
        self.assertIsNone(rows[0]["wayback_pointer"])

    def test_denominator_mismatch_retains_outputs_and_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "inventory.csv")
            path.write_text("Candidate URL,Name,Campaign Website,Status\nhttps://ballotpedia.org/Jane_Doe,Jane Doe,,Not advancing\n")
            args = argparse.Namespace(inventory=path, dataset=None, manifest=[], corpus_root=None, expected_candidates=4319, out=Path(root, "out"))
            with self.assertRaisesRegex(ValueError, "denominator"):
                audit(args)
            summary = json.loads((args.out / "summary.json").read_text())
            self.assertEqual(summary["candidate_count"], 1)
            self.assertFalse(summary["denominator_matches_expectation"])
            self.assertEqual(summary["actions"], {"enrich_campaign_website": 1})

    def test_duplicate_roster_id_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "input.json")
            path.write_text(json.dumps([candidate(), candidate()]))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                inventory(path, {})


class CaptureTests(unittest.TestCase):
    def test_non_html_and_encoded_responses_are_not_accepted_as_campaign_pages(self):
        for headers, body in [({'Content-Type': 'text/html'}, b'An error has occurred.'),
                              ({'Content-Type': 'text/html', 'Content-Encoding': 'br'}, b'encoded')]:
            def transport(url, **kwargs):
                return (404, {}, b'') if url.endswith('/robots.txt') else (200, headers, body)
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
                with self.assertRaises(ValueError):
                    Collector(root, transport=transport, sleeper=lambda _: None).capture({'candidate_id': 'a', 'source_url': 'https://example.org/'})
                self.assertFalse((Path(root) / 'objects').exists())

    def test_bom_robots_rules_are_applied_and_unknown_encoding_stops(self):
        for headers, body in [({}, b'\xef\xbb\xbfUser-agent: *\nDisallow: /\n'),
                              ({'Content-Encoding': 'br'}, b'encoded')]:
            calls = []
            def transport(url, **kwargs):
                calls.append(url)
                return 200, headers, body
            with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
                with self.assertRaises(ValueError):
                    Collector(root, transport=transport, sleeper=lambda _: None).capture({'candidate_id': 'a', 'source_url': 'https://example.org/'})
                self.assertEqual(calls, ['https://example.org/robots.txt'])

    def test_unreviewed_pdf_request_requires_pdf_signature(self):
        def transport(url, **kwargs):
            return (404, {}, b'') if url.endswith('/robots.txt') else (200, {'Content-Type': 'text/html'}, b'<html>Not a PDF</html>')
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
            with self.assertRaisesRegex(ValueError, 'did not return PDF'):
                Collector(root, transport=transport, sleeper=lambda _: None).capture({'candidate_id': 'a', 'source_url': 'https://example.org/plan.pdf', 'kind': 'campaign_pdf', 'require_pdf': True})
            self.assertFalse((Path(root) / 'objects').exists())

    def test_normal_robots_redirect_preserves_rules_and_denials(self):
        for rules in [b'User-agent: *\nAllow: /\n', b'User-agent: *\nDisallow: /issues\n']:
            calls = []
            def transport(url, **kwargs):
                calls.append(url)
                if url == 'https://example.org/robots.txt':
                    return 301, {'Location': 'https://www.example.org/robots.txt'}, b''
                if url.endswith('/robots.txt'):
                    return 200, {}, rules
                return 200, {'Content-Type': 'text/html'}, b'<html>issues</html>'
            with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
                collector = Collector(root, transport=transport, sleeper=lambda _: None)
                if b'Disallow' in rules:
                    with self.assertRaisesRegex(ValueError, 'disallows'):
                        collector.capture({'candidate_id': 'a', 'source_url': 'https://example.org/issues'})
                    self.assertNotIn('https://example.org/issues', calls)
                else:
                    record, _ = collector.capture({'candidate_id': 'a', 'source_url': 'https://example.org/issues'})
                    self.assertEqual(record['robots_checks'][0]['final_url'], 'https://www.example.org/robots.txt')
                    self.assertEqual(len(record['robots_checks'][0]['redirects']), 1)

    def test_robots_redirect_to_access_denial_still_stops(self):
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            return (302, {'Location': '/blocked'}, b'') if url.endswith('/robots.txt') else (403, {}, b'forbidden')
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
            with self.assertRaisesRegex(ValueError, 'robots.txt HTTP 403'):
                Collector(root, transport=transport, sleeper=lambda _: None).capture({'candidate_id': 'a', 'source_url': 'https://example.org/issues'})
        self.assertNotIn('https://example.org/issues', calls)

    def test_robots_longest_match_and_wildcards_prevent_fetch(self):
        for rules in [b"User-agent: *\nAllow: /\nDisallow: /private/\n", b"User-agent: *\nDisallow: /*.pdf$\n"]:
            requests = []
            def transport(url, **kwargs):
                requests.append(url)
                return 200, {}, rules
            with self.subTest(rules=rules), tempfile.TemporaryDirectory() as root, patch("campaign_research.capture.public_url", side_effect=lambda u: u):
                with self.assertRaisesRegex(ValueError, "disallows"):
                    Collector(root, transport=transport, sleeper=lambda _: None).capture(dict(candidate_id="a", source_url="https://example.org/private/policy.pdf"))
            self.assertEqual(requests, ["https://example.org/robots.txt"])

    def test_robots_denial_prevents_source_fetch(self):
        requests = []
        def transport(url, **kwargs):
            requests.append(url)
            return 200, {}, b"User-agent: *\nDisallow: /\n"
        with tempfile.TemporaryDirectory() as root, patch("campaign_research.capture.public_url", side_effect=lambda u: u):
            collector = Collector(root, transport=transport, sleeper=lambda _: None)
            with self.assertRaisesRegex(ValueError, "disallows"):
                collector.capture(dict(candidate_id="a", source_url="https://example.org/policy"))
        self.assertEqual(requests, ["https://example.org/robots.txt"])

    def test_redirect_rechecks_robots_before_new_host(self):
        requests = []
        def transport(url, **kwargs):
            requests.append(url)
            if url.endswith("robots.txt"):
                return (200, {}, b"User-agent: *\nDisallow: /\n") if "new.example" in url else (404, {}, b"")
            return 302, {"Location": "https://new.example/policy"}, b""
        with tempfile.TemporaryDirectory() as root, patch("campaign_research.capture.public_url", side_effect=lambda u: u):
            with self.assertRaisesRegex(ValueError, "disallows"):
                Collector(root, transport=transport, sleeper=lambda _: None).capture(dict(candidate_id="a", source_url="https://example.org/policy"))
        self.assertNotIn("https://new.example/policy", requests)

    def test_html_receipt_and_pdf_discovery_do_not_trigger_recursive_fetch(self):
        requests = []
        body = b'<html><a href="/policy.pdf">Policy</a><a href="https://other.example/other.pdf">Other</a></html>'
        def transport(url, **kwargs):
            requests.append(url)
            return (404, {}, b"") if url.endswith("robots.txt") else (200, {"Content-Type": "text/html"}, body)
        with tempfile.TemporaryDirectory() as root, patch("campaign_research.capture.public_url", side_effect=lambda u: u):
            record, discovered = Collector(root, transport=transport, sleeper=lambda _: None).capture(dict(candidate_id="a", source_url="https://example.org/policy"))
            self.assertEqual(Path(root, record["path"]).read_bytes(), body)
            self.assertEqual(record["sha256"], digest(body))
            self.assertEqual(len(discovered), 1)
            self.assertTrue(discovered[0]["review_required"])
            self.assertEqual(len(requests), 2)

    def test_robots_error_or_html_fails_closed(self):
        for status, body in [(429, b""), (503, b""), (200, b"<html>challenge</html>")]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ValueError):
                    Collector(root, transport=lambda *a, **k: (status, {}, body), sleeper=lambda _: None).allowed("https://example.org/policy")

    def test_source_challenge_is_not_accepted_as_campaign_content(self):
        def transport(url, **kwargs):
            return (404, {}, b"") if url.endswith("robots.txt") else (200, {"Content-Type": "text/html"}, b"<html><title>Just a moment...</title></html>")
        with tempfile.TemporaryDirectory() as root, patch("campaign_research.capture.public_url", side_effect=lambda u: u):
            with self.assertRaisesRegex(ValueError, "access-control"):
                Collector(root, transport=transport, sleeper=lambda _: None).capture(dict(candidate_id="a", source_url="https://example.org/policy"))


class BoundaryTests(unittest.TestCase):
    def test_connection_uses_vetted_dns_and_original_tls_hostname(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        with patch("campaign_research.common.socket.getaddrinfo", side_effect=[[public], [private]]) as dns, patch("campaign_research.common.socket.socket") as make_socket, patch("campaign_research.common.ssl.create_default_context") as context:
            _, addresses = public_target("https://campaign.example/policy")
            connection = VettedHTTPSConnection("campaign.example", addresses)
            connection.connect()
            dns.assert_called_once()
            make_socket.return_value.connect.assert_called_once_with(public[4])
            context.return_value.wrap_socket.assert_called_once_with(make_socket.return_value, server_hostname="campaign.example")

    def test_proxy_cannot_be_bypassed_for_public_capture(self):
        with patch("campaign_research.common.public_target", return_value=("https://campaign.example/policy", [])), patch("campaign_research.common.urllib.request.getproxies", return_value={"https": "http://proxy.example:8080"}), patch("campaign_research.common.urllib.request.proxy_bypass", return_value=False), patch("campaign_research.common.VettedHTTPSConnection") as connection:
            with self.assertRaisesRegex(ValueError, "cannot be bypassed"):
                request("https://campaign.example/policy")
            connection.assert_not_called()

    def test_failed_snapshot_has_receipt_and_no_substitute_dataset(self):
        with tempfile.TemporaryDirectory() as root, patch("research.fetch_public", side_effect=ValueError("Public fetch returned HTTP 403")):
            with self.assertRaisesRegex(ValueError, "403"):
                snapshot(argparse.Namespace(out=root, catalog="https://example.org/catalog.json"))
            self.assertEqual(json.loads(Path(root, "snapshot-receipt.json").read_text())["status"], "blocked_or_error")
            self.assertFalse(Path(root, "dataset.json").exists())

    def test_snapshot_never_overwrites_existing_dataset(self):
        with tempfile.TemporaryDirectory() as root, patch("research.fetch_public") as fetch:
            path = Path(root, "dataset.json")
            path.write_bytes(b"original")
            with self.assertRaisesRegex(ValueError, "fresh"):
                snapshot(argparse.Namespace(out=root, catalog="https://example.org/catalog.json"))
            fetch.assert_not_called()
            self.assertEqual(path.read_bytes(), b"original")

    def test_traversal_and_private_network_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                inside(root, "../outside")
        for url in ["http://example.org", "https://u:p@example.org", "https://127.0.0.1", "https://[::1]", "https://example.org/public/../private", "https://example.org/%2e%2e/private", "https://example.org/private%2Fpolicy.pdf"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_r2_exchange_never_uses_public_prefix(self):
        with patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "fake-test-token"}), patch("campaign_research.storage.request") as request:
            with self.assertRaisesRegex(ValueError, "private/"):
                transfer(argparse.Namespace(direction="push", key="public/catalog.json", file="irrelevant"))
            request.assert_not_called()

    def test_r2_pull_retains_hash_and_retrieval_receipt_without_token(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "fake-test-token"}), patch("campaign_research.storage.request", return_value=(200, {"ETag": "object-etag"}, b"source")):
            target = Path(root, "source.html")
            transfer(argparse.Namespace(direction="pull", key="private/objects/source.html", file=target))
            receipt_text = Path(str(target) + ".r2-receipt.json").read_text()
            receipt = json.loads(receipt_text)
            self.assertEqual(receipt["sha256"], digest(b"source"))
            self.assertEqual(receipt["etag"], "object-etag")
            self.assertNotIn("fake-test-token", receipt_text)

    def test_r2_put_requires_explicit_server_success(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "fake-test-token"}), patch("campaign_research.storage.request", return_value=(200, {}, b'{"success":false}')):
            target = Path(root, "source.html")
            target.write_bytes(b"source")
            with self.assertRaisesRegex(ValueError, "confirm"):
                transfer(argparse.Namespace(direction="push", key="private/objects/source.html", file=target))

    def test_failed_run_upload_has_no_completion_marker(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("a")
            with patch("campaign_research.storage.transfer", side_effect=ValueError("failure")):
                with self.assertRaises(ValueError):
                    upload_run(argparse.Namespace(root=root, key_prefix="private/cloud-runs/test"))
            self.assertFalse(Path(root, "run-manifest.json").exists())


class RobotsTests(unittest.TestCase):
    def test_longest_allow_and_equal_length_allow_tie(self):
        policy = RobotsPolicy("User-agent: *\nAllow: /private/public/\nDisallow: /private/\nDisallow: /same\nAllow: /same\n", "CampaignPreservationAudit/1.0")
        self.assertFalse(policy.can_fetch("https://example.org/private/policy"))
        self.assertTrue(policy.can_fetch("https://example.org/private/public/policy"))
        self.assertTrue(policy.can_fetch("https://example.org/same"))

    def test_specific_groups_merge_without_wildcard_fallback(self):
        policy = RobotsPolicy("User-agent: *\nDisallow: /\nUser-agent: CampaignPreservationAudit\nDisallow: /one\nUser-agent: CampaignPreservationAudit\nDisallow: /two\nCrawl-delay: 4\nRequest-rate: 1/6\n", "CampaignPreservationAudit/1.0")
        self.assertFalse(policy.can_fetch("https://example.org/one"))
        self.assertFalse(policy.can_fetch("https://example.org/two"))
        self.assertTrue(policy.can_fetch("https://example.org/other"))
        self.assertEqual(policy.delay, 6)

    def test_percent_normalization_wildcard_anchors_and_literal_query(self):
        policy = RobotsPolicy("User-agent: *\nDisallow: /%70rivate\nDisallow: /*.pdf$\nDisallow: /search?q=\n", "CampaignPreservationAudit/1.0")
        self.assertFalse(policy.can_fetch("https://example.org/private"))
        self.assertFalse(policy.can_fetch("https://example.org/one/two.pdf"))
        self.assertTrue(policy.can_fetch("https://example.org/one.pdf?download=1"))
        self.assertFalse(policy.can_fetch("https://example.org/search?q=abc"))
        self.assertTrue(policy.can_fetch("https://example.org/searchXq=abc"))

    def test_literal_uri_special_characters_match_percent_encoded_rules(self):
        policy = RobotsPolicy("User-agent: *\nDisallow: /file-%2A.html\nDisallow: /foo-%24\n", "CampaignPreservationAudit/1.0")
        self.assertFalse(policy.can_fetch("https://example.org/file-*.html"))
        self.assertFalse(policy.can_fetch("https://example.org/foo-$"))
        self.assertTrue(policy.can_fetch("https://example.org/file-other.html"))

    def test_unrelated_partial_product_does_not_override_wildcard_group(self):
        policy = RobotsPolicy("User-agent: *\nDisallow: /\nUser-agent: Audit\nAllow: /\n", "CampaignPreservationAudit/1.0")
        self.assertFalse(policy.can_fetch("https://example.org/policy"))


if __name__ == "__main__":
    unittest.main()
