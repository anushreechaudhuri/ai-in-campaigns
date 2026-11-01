"""Resume an explicit private R2 transfer and verify every remote object by SHA-256.

Uses an existing Wrangler login in its current environment. Never exports credentials.
"""
import argparse
import concurrent.futures
import hashlib
import json
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def sha(body):
    return hashlib.sha256(body).hexdigest()


def validate_plan(plan, source):
    prefix = plan['proposed_new_prefix']
    if not prefix.startswith('private/') or not prefix.endswith('/'):
        raise ValueError('Transfer requires a private destination prefix')
    keys = set()
    for row in plan['objects']:
        key = row['key']
        if (not key.startswith(prefix) or key in keys or
                any(p in ('', '.', '..') for p in key.split('/'))):
            raise ValueError('Invalid or duplicate destination key')
        path = source / row['filename']
        if path.is_symlink() or path.resolve().parent != source.resolve():
            raise ValueError('Source must be a direct regular file')
        body = path.read_bytes()
        if len(body) != row['bytes'] or sha(body) != row['sha256']:
            raise ValueError('Local transfer checksum mismatch')
        keys.add(key)
    if (not plan['completion_marker_key'].startswith(prefix) or
            plan['completion_marker_key'] in keys):
        raise ValueError('Completion marker must be a separate private key')


class R2:
    def __init__(self, account, bucket, config):
        self.base = ('https://api.cloudflare.com/client/v4/accounts/' +
                     urllib.parse.quote(account, safe='') + '/r2/buckets/' +
                     urllib.parse.quote(bucket, safe=''))
        self.config = config

    def request(self, key, body=None):
        token = tomllib.loads(self.config.read_text())['oauth_token']
        headers = {'Authorization': 'Bearer ' + token}
        if body is not None:
            headers.update({'Content-Type': 'application/octet-stream',
                            'Cache-Control': 'private, max-age=0',
                            'If-None-Match': '*'})
        url = self.base + '/objects/' + urllib.parse.quote(key, safe='/')
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method='GET' if body is None else 'PUT')
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def ensure(self, key, body):
        expected = sha(body)
        # A transport failure may follow a successful write. Read before retrying.
        for attempt in range(5):
            try:
                status, remote = self.request(key)
                if status == 404:
                    status, response = self.request(key, body)
                    if status not in (200, 201) or not json.loads(response).get('success'):
                        raise ValueError(f'R2 write rejected, HTTP {status}')
                    status, remote = self.request(key)
                if status != 200:
                    raise ValueError(f'R2 read rejected, HTTP {status}')
                if len(remote) != len(body) or sha(remote) != expected:
                    raise ValueError('Remote object mismatch; refusing overwrite')
                return {'key': key, 'bytes': len(remote), 'sha256': expected,
                        'verified_at': datetime.now(timezone.utc).isoformat()}
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if attempt == 4:
                    raise RuntimeError('Storage transport failed after bounded read-before-write retries') from None
                time.sleep(2 ** attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--source-root', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--wrangler-config', required=True, type=Path)
    p.add_argument('--workers', type=int, default=2, choices=range(1, 5))
    p.add_argument('--max-total-bytes', type=int, default=1024 ** 3)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    plan = json.loads(a.plan.read_text())
    validate_plan(plan, a.source_root)
    total = sum(row['bytes'] for row in plan['objects'])
    if total > a.max_total_bytes:
        raise ValueError('Transfer exceeds configured total byte limit')
    print(json.dumps({'local_objects_verified': len(plan['objects']), 'bytes': total,
                      'executing': a.execute}), flush=True)
    if not a.execute:
        return
    a.out.mkdir(parents=True, exist_ok=True)
    client = R2(plan['account_id'], plan['bucket'], a.wrangler_config)
    receipts, lock = [], threading.Lock()

    def put(row):
        rec = client.ensure(row['key'], (a.source_root / row['filename']).read_bytes())
        with lock:
            with (a.out / 'receipts.jsonl').open('a') as f:
                f.write(json.dumps(rec) + '\n')
            receipts.append(rec)
            print(json.dumps({'remote_objects_verified': len(receipts),
                              'bytes': sum(r['bytes'] for r in receipts)}), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(put, plan['objects']))
    marker = {'completed_at': datetime.now(timezone.utc).isoformat(),
              'source_commit': plan.get('source_commit'),
              'manifest_sha256': plan.get('full_capture_manifest_sha256'),
              'objects': sorted(receipts, key=lambda r: r['key']),
              'object_count': len(receipts), 'bytes': total}
    body = (json.dumps(marker, indent=2) + '\n').encode()
    marker_key = plan['completion_marker_key']
    status, existing = client.request(marker_key)
    if status == 200:
        old = json.loads(existing)
        # Resume may change observation times, but never replaces an existing marker.
        shape = lambda m: sorted((r['key'], r['bytes'], r['sha256']) for r in m['objects'])
        if shape(old) != shape(marker):
            raise ValueError('Existing completion marker disagrees with verified plan')
        body = existing
    elif status != 404:
        raise ValueError(f'Completion marker read failed, HTTP {status}')
    client.ensure(marker_key, body)
    (a.out / 'completion.json').write_bytes(body)
    print(json.dumps({'complete': True, 'payload_objects': len(receipts),
                      'payload_bytes': total, 'completion_marker_bytes': len(body)}))


if __name__ == '__main__':
    main()
