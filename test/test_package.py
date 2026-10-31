import hashlib
import importlib.util
import json
import random
import sys
import tarfile
import tempfile
import unittest
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
                records.append({'candidate_id': str(i), 'path': path, 'sha256': sha})
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

    def test_source_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'corpus'
            root.mkdir()
            (root / 'captures.jsonl').write_text(json.dumps({'path': '../outside'}) + '\n')
            with self.assertRaises(ValueError):
                module.package(root, Path(temp) / 'out')
