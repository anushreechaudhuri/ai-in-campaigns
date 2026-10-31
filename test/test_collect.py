import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from campaign_research.collect import Engine, process_candidate


class CollectTests(unittest.TestCase):
    def test_policy_pages_and_linked_pdfs_are_preserved_and_verified(self):
        responses = {
            'https://campaign.example/': b'<html><a href="/issues">Issues</a><a href="/privacy-policy">Privacy policy</a></html>',
            'https://campaign.example/issues': b'<html><a href="/platform.pdf">Platform</a><a href="/issues/privacy">Data privacy</a></html>',
            'https://campaign.example/issues/privacy': b'<html>Privacy rights platform</html>',
            'https://campaign.example/platform.pdf': b'%PDF-1.7 platform',
        }
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            if url.endswith('/robots.txt'):
                return 404, {}, b''
            return 200, {'Content-Type': 'application/pdf' if url.endswith('.pdf') else 'text/html'}, responses[url]
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
            Path(root, 'checkpoints').mkdir()
            engine = Engine(root, 100000, 100, transport=transport, sleeper=lambda _: None)
            state = process_candidate(engine, {'candidate_id': 'https://ballotpedia.org/Example', 'name': 'Example', 'website': 'https://campaign.example/'}, 6, 4)
            self.assertEqual(state['status'], 'success')
            self.assertEqual(state['policy_pages'], 2)
            self.assertEqual(state['policy_pdf_candidates'], 1)
            self.assertNotIn('https://campaign.example/privacy-policy', calls)
            self.assertTrue(all(r.get('verified_at') for r in state['records']))
            self.assertEqual(state['records'][-1]['kind'], 'campaign_pdf')

    def test_blocked_reference_robots_are_cached_and_not_missing_site(self):
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            return 202, {}, b''
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
            Path(root, 'checkpoints').mkdir()
            engine = Engine(root, 100000, 100, transport=transport, sleeper=lambda _: None)
            for name in ['One', 'Two']:
                state = process_candidate(engine, {'candidate_id': 'https://ballotpedia.org/' + name, 'name': name, 'website': ''}, 6, 4)
                self.assertEqual(state['status'], 'discovery_failed')
            self.assertEqual(calls, ['https://ballotpedia.org/robots.txt'])

    def test_reference_campaign_link_has_provenance_and_other_links_are_ignored(self):
        def transport(url, **kwargs):
            if url.endswith('/robots.txt'):
                return 404, {}, b''
            if 'ballotpedia.org' in url:
                return 200, {'Content-Type': 'text/html'}, b'<html><a href="https://unrelated.example/">Website</a><a href="https://campaign.example/">Campaign website</a></html>'
            return 200, {'Content-Type': 'text/html'}, b'<html>Home only</html>'
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u: u):
            Path(root, 'checkpoints').mkdir()
            state = process_candidate(Engine(root, 100000, 100, transport=transport, sleeper=lambda _: None), {'candidate_id': 'https://ballotpedia.org/Example', 'name': 'Example', 'website': ''}, 6, 4)
            self.assertEqual(state['resolved_website'], 'https://campaign.example/')
            self.assertEqual(state['status'], 'no_policy_found_in_bounded_scan')
            self.assertEqual(state['records'][0]['kind'], 'reference_page')
            self.assertIn('sha256', state['website_evidence'])


if __name__ == '__main__':
    unittest.main()
