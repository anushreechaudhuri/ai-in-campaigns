import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('directory_policy', Path(__file__).resolve().parents[1] / 'scripts/cloud-directory-policy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DirectoryIdentityTests(unittest.TestCase):
    def sample(self):
        body = b'''<html><h1>Jane Example</h1><dl>
        <dt>FEC candidate ID</dt><dd>H6CO02206</dd>
        <dt>Office sought</dt><dd>U.S. House - District 2, Colorado</dd>
        <dt>Election year</dt><dd>2026</dd>
        <dt>Website</dt><dd><a href="https://jane.example/">Campaign website</a></dd></dl>
        <a href="https://jane.example/issues/housing">Campaign website - Housing</a>
        <a href="https://jane.example/privacy-policy">Privacy policy</a></html>'''
        profile = {'directory_name': 'Jane Example', 'directory_url': 'https://saveyourstate.com/candidates/co/h6co02206/',
                   'fec_candidate_id': 'H6CO02206', 'identity_join_method': 'Exact official FEC identity'}
        candidate = {'name': 'Jane Example', 'state': 'CO', 'chamber': 'House', 'district': '2'}
        return body, profile, candidate

    def test_extracts_explicit_candidate_links_without_using_directory_summary(self):
        body, profile, candidate = self.sample()
        identity, links = module.validate_profile(body, profile, candidate)
        self.assertEqual(identity['website'], 'https://jane.example/')
        self.assertIn('https://jane.example/issues/housing', links)
        self.assertNotIn('https://jane.example/privacy-policy', links)

    def test_rejects_name_fec_office_state_district_and_cycle_mismatches(self):
        body, profile, candidate = self.sample()
        for old, new in [(b'Jane Example', b'Other Person'), (b'H6CO02206', b'H6CO99999'),
                         (b'U.S. House', b'U.S. Senate'), (b'Colorado', b'Florida'),
                         (b'District 2', b'District 3'), (b'2026', b'2028')]:
            with self.assertRaises(ValueError):
                module.validate_profile(body.replace(old, new), profile, candidate)

    def test_equivalent_hosts_and_tracking_parameters_do_not_create_new_capture_urls(self):
        self.assertEqual(module.normalized_url('http://www.jane.example/issues/?utm_source=feed'),
                         module.normalized_url('https://jane.example/issues'))

    def test_resume_retains_config_and_rejects_corrupted_saved_bytes_before_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input.json'
            raw = json.dumps({'profiles': [], 'candidates': [], 'seen_urls': {}, 'blocked_source_origins': {}}).encode()
            source.write_bytes(raw)
            out = root / 'run'
            args = ['directory', '--bridge', 'https://unused.example', '--input-key', 'private/input',
                    '--input-sha256', module.digest(raw), '--write-prefix', 'private/cloud-runs/test/',
                    '--fallback-input', str(source), '--out', str(out)]
            env = {'GITHUB_SHA': 'workflow', 'CODE_COMMIT': 'code', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
            with patch.dict(os.environ, env), patch.object(module.cloud_batch.Bridge, 'get', side_effect=RuntimeError('Unavailable')):
                with patch.object(sys, 'argv', args):
                    module.main()
                original = (out / 'run-config.json').read_bytes()
                with patch.object(sys, 'argv', args + ['--resume']):
                    module.main()
                self.assertEqual((out / 'run-config.json').read_bytes(), original)
                self.assertEqual(json.loads((out / 'summary.json').read_text())['lifetime_network_requests'], 0)
                (out / 'objects').mkdir()
                (out / 'objects/source.html').write_bytes(b'changed')
                (out / 'captures.jsonl').write_text(json.dumps({'candidate_id': 'c', 'kind': 'campaign_page',
                                                               'source_url': 'https://example.org/issues',
                                                               'sha256': module.digest(b'original'), 'bytes': 8,
                                                               'path': 'objects/source.html'}) + '\n')
                with patch.object(sys, 'argv', args + ['--resume']), self.assertRaisesRegex(ValueError, 'Resume source bytes'):
                    module.main()
