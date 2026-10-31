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

    def test_reference_pages_do_not_count_as_preserved_campaign_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            record = self.fixture(root / 'a')
            record['kind'] = 'reference_page'
            (root / 'a/captures.jsonl').write_text(json.dumps(record) + '\n')
            summary = module.merge(inventory, [('run1', root / 'a')], root / 'out')
            self.assertEqual(summary['candidate_ids_with_captured_bytes'], 0)
            row = json.loads((root / 'out/coverage-ledger.json').read_text())['candidates'][0]
            self.assertTrue(row['reference_bytes_preserved'])
            self.assertFalse(row['source_bytes_preserved'])

    def test_hash_valid_error_response_is_diagnostic_not_campaign_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            self.fixture(root / 'a', b'An error has occurred.')
            summary = module.merge(inventory, [('run1', root / 'a')], root / 'out')
            self.assertEqual(summary['candidate_ids_with_captured_bytes'], 0)
            self.assertEqual(summary['unusable_response_receipts'], 1)
            self.assertEqual(len(json.loads((root / 'out/unusable-response-receipts.json').read_text())), 1)
            self.assertEqual(len(list((root / 'out/objects').glob('*'))), 1)

    def test_rejects_duplicate_ids_and_missing_runs_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'same'}, {'candidate_id': 'same'}]}))
            with self.assertRaisesRegex(ValueError, 'unique nonempty'):
                module.merge(inventory, [('run1', root / 'absent')], root / 'out')
            self.assertFalse((root / 'out').exists())
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'one'}]}))
            with self.assertRaisesRegex(ValueError, 'run input is missing'):
                module.merge(inventory, [('run1', root / 'absent')], root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_rejects_metadata_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            self.fixture(root / 'a')
            outside = root / 'outside.json'
            outside.write_text('{"private":true}')
            (root / 'a/run-config.json').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                module.merge(inventory, [('run1', root / 'a')], root / 'out')

    def test_discovery_outcomes_keep_verified_index_without_inflating_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            source = root / 'reference'
            source.mkdir()
            body = b'[]'
            (source / 'index.json').write_bytes(body)
            state = {'candidate_id': 'candidate1', 'status': 'no_matching_archived_reference_in_date_range',
                     'index_path': 'index.json', 'index_sha256': hashlib.sha256(body).hexdigest()}
            (source / 'discovery-results.json').write_text(json.dumps([state]))
            summary = module.merge(inventory, [('reference', source)], root / 'out')
            self.assertEqual(summary['candidate_ids_with_captured_bytes'], 0)
            self.assertEqual(summary['discovery_attempts'], 1)
            row = json.loads((root / 'out/coverage-ledger.json').read_text())['candidates'][0]
            copied = row['discovery_attempts'][0]
            self.assertEqual((root / 'out' / copied['index_path']).read_bytes(), body)
            (source / 'index.json').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'index checksum'):
                module.merge(inventory, [('reference', source)], root / 'tampered-out')

    def test_discovery_outcome_unknown_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            source = root / 'reference'
            source.mkdir()
            (source / 'discovery-results.json').write_text(json.dumps([{'candidate_id': 'other'}]))
            with self.assertRaisesRegex(ValueError, 'Discovery candidate absent'):
                module.merge(inventory, [('reference', source)], root / 'out')

    def test_checksum_bound_scope_review_retains_bytes_excludes_unrelated_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'inventory.json'
            inventory.write_text(json.dumps({'candidates': [{'candidate_id': 'candidate1'}]}))
            record = self.fixture(root / 'a')
            review = root / 'review.json'
            decision = {'candidate_id': 'candidate1', 'sha256': record['sha256'],
                        'decision': 'unrelated_response', 'reason': 'Repurposed domain, unrelated organization'}
            review.write_text(json.dumps({'decisions': [decision]}))
            summary = module.merge(inventory, [('run1', root / 'a')], root / 'out', review)
            self.assertEqual(summary['candidate_ids_with_captured_bytes'], 0)
            self.assertEqual(summary['scope_excluded_response_receipts'], 1)
            self.assertEqual(summary['unique_bodies'], 1)
            self.assertEqual(summary['unique_usable_campaign_body_bytes'], 0)
            second = module.merge(inventory, [('old', root / 'a'), ('reviewed', root / 'out')], root / 'second')
            self.assertEqual(second['candidate_ids_with_captured_bytes'], 0)
            self.assertEqual(second['scope_excluded_response_receipts'], 1)
            third = module.merge(inventory, [('reviewed', root / 'out')], root / 'third')
            self.assertEqual(third['candidate_ids_with_captured_bytes'], 0)
            decision['sha256'] = '0' * 64
            review.write_text(json.dumps({'decisions': [decision]}))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                module.merge(inventory, [('run1', root / 'a')], root / 'unmatched', review)
