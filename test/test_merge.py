import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('merge_preservation', Path(__file__).resolve().parents[1] / 'scripts/merge-preservation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MergeTests(unittest.TestCase):
    def fixture(self, root, body=b'<html>policy</html>'):
        sha = hashlib.sha256(body).hexdigest()
        root.mkdir()
        (root / 'body.html').write_bytes(body)
        record = {'candidate_id': 'candidate1', 'status': 'captured', 'source_url': 'https://example.org/issues',
                  'final_url': 'https://example.org/issues', 'kind': 'campaign_page', 'sha256': sha,
                  'path': 'body.html', 'bytes': len(body), 'captured_at': '2026-10-31T00:00:00Z'}
        (root / 'captures.jsonl').write_text(json.dumps(record) + '\n')
        return record

    def test_deduplicates_bytes_preserves_run_observations_and_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}, {'candidate_id': 'candidate2'}]}))
            self.fixture(root / 'a')
            self.fixture(root / 'b')
            summary = module.merge(inventory, [('run1', root / 'a'), ('run2', root / 'b')], root / 'out')
            self.assertEqual((summary['deduplicated_receipts'], summary['unique_bodies']), (1, 1))
            self.assertEqual(summary['candidate_ids_without_captured_bytes'], 1)
            record = json.loads((root / 'out/captures.jsonl').read_text())
            self.assertEqual([r['run_id'] for r in record['observations']], ['run1', 'run2'])
            ledger = json.loads((root / 'out/coverage-ledger.json').read_text())['candidates']
            self.assertEqual(ledger[0]['current_candidate_site_identity'], 'requires_review')
            self.assertFalse(summary['scope_complete'])

    def test_refuses_tampered_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            self.fixture(root / 'a')
            (root / 'a/body.html').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum/length'):
                module.merge(inventory, [('run1', root / 'a')], root / 'out')
