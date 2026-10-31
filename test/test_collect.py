import json
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from campaign_research.collect import Engine, process_candidate, collect
from campaign_research.common import ReadFailure


class CollectTests(unittest.TestCase):
    def test_path_denial_does_not_poison_allowed_paths(self):
        calls=[]
        def transport(url, **kwargs):
            calls.append(url)
            if url.endswith('/robots.txt'):
                return 200, {}, b'User-agent: *\nDisallow: /issues\n'
            return 200, {'Content-Type':'text/html'}, b'<html>Platform</html>'
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url', side_effect=lambda u:u):
            engine=Engine(root,100000,100,transport=transport,sleeper=lambda _:None)
            with self.assertRaisesRegex(ValueError,'disallows'):
                engine.acquire('a','https://campaign.example/issues','campaign_page',[])
            engine.acquire('a','https://campaign.example/platform','campaign_page',[])
            self.assertIn('https://campaign.example/platform',calls)

    def test_effective_crawl_delay_is_enforced_at_each_transport_request(self):
        clock=[100.0];calls=[]
        def sleep(seconds):clock[0]+=seconds
        def transport(url, **kwargs):
            calls.append((url,clock[0]))
            return (200,{},b'User-agent: *\nCrawl-delay: 10\n') if url.endswith('/robots.txt') else (200,{'Content-Type':'text/html'},b'<html>Policy</html>')
        with tempfile.TemporaryDirectory() as root, patch('campaign_research.capture.public_url',side_effect=lambda u:u),patch('campaign_research.collect.time.monotonic',side_effect=lambda:clock[0]):
            engine=Engine(root,100000,100,transport=transport,sleeper=sleep)
            engine.allowed('https://campaign.example/one')
            engine.allowed('https://campaign.example/two')
            engine.gated_request('https://campaign.example/one')
            engine.gated_request('https://campaign.example/two')
            self.assertGreaterEqual(calls[-1][1]-calls[-2][1],10)

    def test_oversized_failures_consume_reserved_body_budget(self):
        def transport(url,**kwargs):raise ReadFailure('oversized',kwargs['max_bytes']+1)
        with tempfile.TemporaryDirectory() as root:
            engine=Engine(root,100,100,transport=transport,sleeper=lambda _:None)
            with self.assertRaises(ReadFailure):engine.gated_request('https://campaign.example/a')
            self.assertEqual(engine.downloaded,100)
            self.assertTrue(engine.exhausted)
            with self.assertRaisesRegex(ValueError,'budget'):engine.gated_request('https://campaign.example/b')
            self.assertEqual(engine.requests,1)

    def test_extensionless_pdf_does_not_bypass_zero_pdf_cap(self):
        def transport(url,**kwargs):
            if url.endswith('/robots.txt'):return 404,{},b''
            if url.endswith('/issues'):return 200,{'Content-Type':'application/pdf'},b'%PDF-1.7 policy'
            return 200,{'Content-Type':'text/html'},b'<html><a href="/issues">Issues</a></html>'
        with tempfile.TemporaryDirectory() as root,patch('campaign_research.capture.public_url',side_effect=lambda u:u):
            Path(root,'checkpoints').mkdir()
            state=process_candidate(Engine(root,100000,100,transport=transport,sleeper=lambda _:None),{'candidate_id':'https://ballotpedia.org/Example','name':'Example','website':'https://campaign.example/'},6,0)
            self.assertEqual(state['policy_pdf_candidates'],0)
            self.assertEqual(state['unexpected_pdf_responses_skipped'],1)
            self.assertFalse(any(r['kind']=='campaign_pdf' for r in state['records']))
            self.assertEqual(state['status'],'partial')

    def test_unvisited_policy_links_are_partial_not_absence(self):
        def transport(url,**kwargs):return (404,{},b'') if url.endswith('/robots.txt') else (200,{'Content-Type':'text/html'},b'<html><a href="/issues">Issues</a></html>')
        with tempfile.TemporaryDirectory() as root,patch('campaign_research.capture.public_url',side_effect=lambda u:u):
            Path(root,'checkpoints').mkdir()
            state=process_candidate(Engine(root,100000,100,transport=transport,sleeper=lambda _:None),{'candidate_id':'https://ballotpedia.org/Example','name':'Example','website':'https://campaign.example/'},1,0)
            self.assertEqual(state['status'],'partial')
            self.assertEqual(state['unvisited_policy_links'],1)

    def test_resume_rejects_changed_input_without_overwriting_run_config(self):
        def transport(url,**kwargs):return (404,{},b'') if url.endswith('/robots.txt') else (200,{'Content-Type':'text/html'},b'<html>Home</html>')
        original_engine=Engine
        with tempfile.TemporaryDirectory() as root,patch('campaign_research.capture.public_url',side_effect=lambda u:u),patch('campaign_research.collect.Engine',side_effect=lambda out,size,count:original_engine(out,size,count,transport=transport,sleeper=lambda _:None)):
            path=Path(root,'seed.json');out=Path(root,'out')
            row={'candidate_id':'https://ballotpedia.org/Example','name':'Example','website':'https://old.example/'}
            path.write_text(json.dumps([row]))
            args=argparse.Namespace(inventory=path,out=out,dataset=None,limit=None,workers=1,max_pages=1,max_pdfs=0,max_mib=1,max_requests=100,retry_failures=False)
            collect(args)
            before=(out/'run-config.json').read_bytes()
            path.write_text(json.dumps([{**row,'website':'https://new.example/'}]))
            with self.assertRaisesRegex(ValueError,'different input/config/code'):collect(args)
            self.assertEqual((out/'run-config.json').read_bytes(),before)

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
