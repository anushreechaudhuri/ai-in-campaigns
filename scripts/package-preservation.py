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
    root, out = Path(root).resolve(), Path(out)
    if out.exists():
        raise ValueError('Use a new delivery directory')
    out.mkdir(parents=True)
    expected = {}
    def read_checked(relative, sha256=None, length=None):
        if (root / relative).is_symlink():
            raise ValueError('Corpus symlink refused')
        path = inside(root, relative)
        body = path.read_bytes()
        computed = hashlib.sha256(body).hexdigest()
        declared = expected.get(relative)
        if sha256 is not None and computed != sha256 or length is not None and len(body) != length:
            raise ValueError('Capture receipt checksum/length mismatch')
        if declared and (computed != declared['sha256'] or len(body) != declared['bytes']):
            raise ValueError('Merged corpus checksum/length mismatch')
        return body
    if (root / 'sha256sums.json').exists():
        checks = json.loads(read_checked('sha256sums.json'))
        expected = {r['path']: r for r in checks}
        if len(expected) != len(checks):
            raise ValueError('Duplicate corpus checksum paths')
    records = [json.loads(line) for line in read_checked('captures.jsonl').decode().splitlines()]
    for record in records:
        record['path'] = str(inside(root, record['path']).relative_to(root))
        for observation in [record] + record.get('observations', []):
            for check in observation.get('robots_checks', []):
                check['path'] = str(inside(root, check['path']).relative_to(root))
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
        files = {}
        def declare(relative, sha256, length=None):
            if relative in files:
                prior_sha, prior_length = files[relative]
                if prior_sha != sha256 or prior_length is not None and length is not None and prior_length != length:
                    raise ValueError('Conflicting capture receipt hashes/lengths')
            files[relative] = (sha256, length)
        for record in rows:
            declare(record['path'], record['sha256'], record.get('bytes'))
            for observation in [record] + record.get('observations', []):
                for check in observation.get('robots_checks', []):
                    declare(check['path'], check['sha256'])
        with path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as tar:
            for relative in sorted(files):
                add(tar, relative, read_checked(relative, *files[relative]))
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
    if (root / 'run-metadata').is_symlink():
        raise ValueError('Run metadata symlink refused')
    with zipfile.ZipFile(coverage, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for filename in ('coverage-ledger.json', 'merge-summary.json', 'pdf-verification.json', 'failed-captures.json'):
            target.writestr(filename, read_checked(filename))
        for path in sorted((root / 'run-metadata').rglob('*')):
            if path.is_symlink():
                raise ValueError('Run metadata symlink refused')
            if path.is_file():
                relative = str(path.relative_to(root))
                target.writestr(relative, read_checked(relative))
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
