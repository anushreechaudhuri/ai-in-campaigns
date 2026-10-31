#!/usr/bin/env python3
"""Make independent private source archives within a delivery size limit."""
import argparse
import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from campaign_research.common import inside, write_json


def package(root, out, max_bytes=20_000_000):
    root, out = Path(root), Path(out)
    if out.exists():
        raise ValueError('Use a new delivery directory')
    out.mkdir(parents=True)
    records = [json.loads(line) for line in (root / 'captures.jsonl').read_text().splitlines()]
    note = ('Private campaign source preservation archive. Each numbered archive is independently extractable.\n'
            'captures.jsonl contains source URLs, times, SHA-256 hashes and run observations.\n'
            'objects/ contains response bodies; robots/ contains corresponding policy receipts.\n'
            'Captured bytes do not establish complete policy coverage, current candidate identity or AI authorship.\n'
            'PDF candidates may include third-party references and require semantic review.\n'
            'The companion coverage archive contains the full candidate ledger and source-run metadata.\n')
    def add(tar, name, body):
        info = tarfile.TarInfo(name)
        info.mode, info.size, info.mtime = 0o600, len(body), 0
        tar.addfile(info, io.BytesIO(body))
    def make(rows, path):
        files = {r['path'] for r in rows}
        for record in rows:
            for observation in [record] + record.get('observations', []):
                files.update(c['path'] for c in observation.get('robots_checks', []))
        with path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as tar:
            for relative in sorted(files):
                path = inside(root, relative)
                add(tar, relative, path.read_bytes())
            add(tar, 'captures.jsonl', ''.join(json.dumps(r) + '\n' for r in rows).encode())
            add(tar, 'README.txt', note.encode())
    queue, saved = [records] if records else [], []
    while queue:
        rows = queue.pop(0)
        path = out / ('candidate-policy-batch-%02d.tar.gz' % (len(saved) + 1))
        make(rows, path)
        if path.stat().st_size > max_bytes:
            path.unlink()
            middle = len(rows) // 2
            if not middle:
                raise ValueError('A single response exceeds the delivery cap; retain full private source archive')
            queue[0:0] = [rows[:middle], rows[middle:]]
        else:
            saved.append({'filename': path.name, 'bytes': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'capture_receipts': len(rows), 'candidate_ids': len({r['candidate_id'] for r in rows})})
    coverage = out / 'coverage-and-readme.zip'
    with zipfile.ZipFile(coverage, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for filename in ('coverage-ledger.json', 'merge-summary.json', 'pdf-verification.json', 'failed-captures.json'):
            target.write(root / filename, filename)
        for path in sorted((root / 'run-metadata').rglob('*')):
            if path.is_file():
                target.write(path, str(path.relative_to(root)))
        target.writestr('README.txt', note)
        target.writestr('download-batches.json', json.dumps(saved, indent=2) + '\n')
    if coverage.stat().st_size > max_bytes:
        raise ValueError('Coverage metadata exceeds delivery cap')
    saved.append({'filename': coverage.name, 'bytes': coverage.stat().st_size,
                  'sha256': hashlib.sha256(coverage.read_bytes()).hexdigest()})
    write_json(out / 'delivery-index.json', saved)
    return saved


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-bytes', type=int, default=20_000_000)
    args = parser.parse_args()
    if args.max_bytes < 1:
        parser.error('--max-bytes must be positive')
    print(json.dumps(package(args.corpus, args.out, args.max_bytes)))
