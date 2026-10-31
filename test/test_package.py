import hashlib
import importlib.util
import json
import random
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('package_preservation', Path(__file__).resolve().parents[1] / 'scripts/package-preservation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def test_split_archives_are_independent_and_within_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'corpus'
            (root / 'objects').mkdir(parents=True)
            records = []
            rng = random.Random(11)
            for i in range(3):
                body = rng.randbytes(12000)
                sha = hashlib.sha256(body).hexdigest()
                path = 'objects/' + sha + '.html'
                (root / path).write_bytes(body)
                records.append({'candidate_id': str(i), 'path': path, 'sha256': sha,
                                'kind': 'campaign_page', 'source_content_valid': True,
                                'source_scope_excluded': False})
            (root / 'captures.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
            for filename in ('coverage-ledger.json', 'merge-summary.json', 'pdf-verification.json', 'failed-captures.json'):
                (root / filename).write_text('{}')
            out = Path(temp) / 'delivery'
            saved = module.package(root, out, 16000)
            archives = [r for r in saved if r['filename'].endswith('.tar.gz')]
            self.assertEqual(len(archives), 3)
            self.assertTrue(all(r['bytes'] <= 16000 for r in saved))
            for i, row in enumerate(archives):
                destination = Path(temp) / str(i)
                with tarfile.open(out / row['filename']) as archive:
                    archive.extractall(destination, filter='data')
                for line in (destination / 'captures.jsonl').read_text().splitlines():
                    record = json.loads(line)
                    self.assertEqual(hashlib.sha256((destination / record['path']).read_bytes()).hexdigest(), record['sha256'])

    def test_only_verified_campaign_bodies_are_exported_and_omissions_are_auditable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'corpus'
            root.mkdir()
            records = []
            for i, extra in enumerate([
                    {'kind': 'campaign_pdf', 'source_content_valid': True, 'source_scope_excluded': False},
                    {'kind': 'reference_page', 'source_content_valid': True, 'source_scope_excluded': False},
                    {'kind': 'campaign_page', 'source_content_valid': False, 'source_scope_excluded': False},
                    {'kind': 'campaign_page', 'source_content_valid': True, 'source_scope_excluded': True},
                    {'kind': 'campaign_page'}]):
                body = ('source-' + str(i)).encode()
                path = str(i) + '.body'
                (root / path).write_bytes(body)
                records.append({'candidate_id': str(i), 'path': path,
                                'sha256': hashlib.sha256(body).hexdigest(), **extra})
            (root / 'captures.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
            for filename in ('coverage-ledger.json', 'merge-summary.json', 'pdf-verification.json', 'failed-captures.json'):
                (root / filename).write_text('{}')
            out = Path(temp) / 'delivery'
            saved = module.package(root, out)
            with tarfile.open(out / saved[0]['filename']) as archive:
                self.assertIn('0.body', archive.getnames())
                self.assertTrue(all(str(i) + '.body' not in archive.getnames() for i in range(1, 5)))
                self.assertEqual(len(archive.extractfile('captures.jsonl').read().splitlines()), 1)
            with zipfile.ZipFile(out / 'coverage-and-readme.zip') as archive:
                omissions = json.loads(archive.read('omitted-capture-receipts.json'))
                self.assertEqual(omissions, records[1:])
                self.assertNotIn('1.body', archive.namelist())
            (root / '1.body').write_bytes(b'tampered reference')
            with self.assertRaisesRegex(ValueError, 'checksum/length'):
                module.package(root, Path(temp) / 'changed-omitted-body')

    def test_source_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'corpus'
            root.mkdir()
            (root / 'captures.jsonl').write_text(json.dumps({'path': '../outside'}) + '\n')
            with self.assertRaises(ValueError):
                module.package(root, Path(temp) / 'out')

    def test_tampered_source_and_metadata_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'corpus'
            root.mkdir()
            (root / 'body.html').write_bytes(b'changed')
            record = {'path': 'body.html', 'sha256': hashlib.sha256(b'original').hexdigest()}
            (root / 'captures.jsonl').write_text(json.dumps(record) + '\n')
            with self.assertRaisesRegex(ValueError, 'checksum/length'):
                module.package(root, Path(temp) / 'tampered-out')
            (root / 'captures.jsonl').write_text('')
            for filename in ('coverage-ledger.json', 'merge-summary.json', 'pdf-verification.json', 'failed-captures.json'):
                (root / filename).write_text('{}')
            (root / 'run-metadata').mkdir()
            outside = Path(temp) / 'outside.json'
            outside.write_text('{"private":true}')
            (root / 'run-metadata/extra.json').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                module.package(root, Path(temp) / 'symlink-out')
