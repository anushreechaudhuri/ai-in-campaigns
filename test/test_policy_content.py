import hashlib
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('policy_review', Path(__file__).resolve().parents[1] / 'scripts/review-policy-content.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PolicyContentTests(unittest.TestCase):
    def row(self, url, body):
        return {'candidate_id': 'canonical', 'source_url': url,
                'sha256': hashlib.sha256(body).hexdigest()}

    def test_shells_and_script_payloads_are_not_substantive_policy_text(self):
        body = b'<html><h1>Issues</h1><script>' + b'health education 2026 campaign ' * 2000 + b'</script></html>'
        result, text = module.review(self.row('https://example.org/issues', body), body,
                                     {'name': 'Jane Example', 'chamber': 'House'})
        self.assertTrue(result['policy_url_signal'])
        self.assertFalse(result['saved_policy_text_candidate'])
        self.assertNotIn('campaign', text)

    def test_substantive_text_identity_and_cycle_are_separate(self):
        body = ('<html><h1>Jane Example for Congress, 2026</h1><p>' + 'Education housing healthcare jobs. ' * 60 + '</p></html>').encode()
        result, _ = module.review(self.row('https://example.org/issues', body), body,
                                  {'name': 'Jane Example', 'chamber': 'House'})
        self.assertTrue(result['saved_policy_text_candidate'])
        self.assertTrue(result['machine_current_race_evidence'])
        self.assertEqual(result['policy_completeness'], 'not_established')
        result, _ = module.review(self.row('https://example.org/privacy-policy', body), body,
                                  {'name': 'Jane Example', 'chamber': 'House'})
        self.assertFalse(result['saved_policy_text_candidate'])
        self.assertFalse(result['policy_url_signal'])

    def test_parked_body_and_hash_mismatch_are_not_campaign_policy(self):
        body = b'<html><p>This domain is for sale</p></html>'
        result, _ = module.review(self.row('https://example.org/issues', body), body,
                                  {'name': 'Jane Example', 'chamber': 'House'})
        self.assertEqual(result['scope_status'], 'excluded_parked_response')
        with self.assertRaises(ValueError):
            module.review(self.row('https://example.org/issues', body), body + b'changed',
                          {'name': 'Jane Example', 'chamber': 'House'})
