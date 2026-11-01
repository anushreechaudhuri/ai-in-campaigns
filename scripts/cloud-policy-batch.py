"""Capture new, provenance-linked policy URLs; checkpoint to R2 using job OIDC.

No saved Cloudflare credential, detector request, or dashboard publication.
"""
import argparse
import collections
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from campaign_research.collect import Engine, origin, validate_controls
from campaign_research.common import digest, now, write_json

spec = importlib.util.spec_from_file_location('content_review', Path(__file__).with_name('review-policy-content.py'))
content_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(content_review)


class Bridge:
    def __init__(self, url):
        self.url = url.rstrip('/')

    def token(self):
        query = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
        query += ('&' if '?' in query else '?') + urllib.parse.urlencode({'audience': self.url})
        req = urllib.request.Request(query, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)['value']

    def exchange(self, key, body=None):
        headers = {'Authorization': 'Bearer ' + self.token()}
        if body is not None:
            headers['X-Content-SHA256'] = digest(body)
            headers['Content-Type'] = 'application/octet-stream'
        req = urllib.request.Request(self.url + '/object?' + urllib.parse.urlencode({'key': key}), data=body,
                                     headers=headers, method='GET' if body is None else 'PUT')
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def get(self, key):
        status, body = self.exchange(key)
        if status != 200:
            raise RuntimeError(f'Research input read failed, HTTP {status}')
        return body

    def put(self, key, body):
        for attempt in range(4):
            try:
                status, response = self.exchange(key, body)
                if status not in (200, 201):
                    raise RuntimeError(f'Research output rejected, HTTP {status}')
                status, remote = self.exchange(key)
                if status != 200 or remote != body:
                    raise RuntimeError('Research output remote readback mismatch')
                return {'key': key, 'bytes': len(body), 'sha256': digest(body), 'verified_at': now()}
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise RuntimeError('Research bridge transport failed') from None
                time.sleep(2 ** attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bridge', required=True)
    p.add_argument('--input-key', required=True)
    p.add_argument('--input-sha256', required=True)
    p.add_argument('--write-prefix', required=True)
    p.add_argument('--shard', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-seconds', type=int, default=2400)
    a = p.parse_args()
    if not 1 <= a.max_seconds <= 3600 or not a.write_prefix.startswith('private/cloud-runs/'):
        raise ValueError('Invalid bounded research run')
    a.out.mkdir(parents=True, exist_ok=False)
    bridge = Bridge(a.bridge)
    raw = bridge.get(a.input_key)
    if digest(raw) != a.input_sha256:
        raise ValueError('Private input checksum mismatch')
    payload = json.loads(raw)
    rows = [r for r in payload['sources'] if r['shard'] == a.shard]
    if len(rows) > 250:
        raise ValueError('Use at most 250 source URLs per shard')
    by_id = {c['candidate_id']: c for c in payload['candidates']}
    controls = validate_controls(payload['blocked_source_origins'])
    seen = set()
    for row in rows:
        if row['candidate_id'] not in by_id or (row['candidate_id'], row['source_url']) in seen:
            raise ValueError('Invalid or duplicate canonical candidate source')
        if not re.fullmatch('[a-f0-9]{64}', row['reference_sha256']):
            raise ValueError('Source has no preserved parent-body hash')
        seen.add((row['candidate_id'], row['source_url']))
    write_json(a.out / 'input.json', payload)
    engine = Engine(a.out, 512 * 1024 * 1024, 2000)
    for url, reason in controls.items():
        engine.pause_origin(url, reason, inherited=True)
    engine.save_controls()
    config = {'input_sha256': digest(raw), 'code_commit': os.environ.get('CODE_COMMIT'),
              'workflow_commit': os.environ.get('GITHUB_SHA'), 'shard': a.shard,
              'max_seconds': a.max_seconds, 'max_body_bytes': engine.max_bytes,
              'max_requests': engine.max_requests, 'sources': len(rows), 'started_at': now()}
    engine.identity = digest(json.dumps(config, sort_keys=True).encode())
    write_json(a.out / 'run-config.json', config)
    deadline = time.monotonic() + a.max_seconds
    upstream = engine.upstream

    def bounded(url, **kwargs):
        if time.monotonic() >= deadline:
            raise ValueError('Job time budget reached; no new source requests')
        return upstream(url, **kwargs)

    engine.upstream = bounded
    states, lock = [], threading.Lock()
    root_prefix = a.write_prefix + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '/' + str(a.shard) + '/'
    uploaded = {}

    def upload_checkpoint():
        receipts = []
        # Content-addressed output versions avoid every destructive overwrite.
        for path in sorted(a.out.rglob('*')):
            if not path.is_file() or '.partial-' in path.name or path.name == 'r2-readback.json':
                continue
            body = path.read_bytes()
            if len(body) > 20000000:
                raise ValueError('Output exceeds independent object limit')
            relative = str(path.relative_to(a.out))
            key = root_prefix + 'files/' + digest(body) + '-' + relative.replace('/', '--')
            if key not in uploaded:
                uploaded[key] = bridge.put(key, body)
            receipts.append({**uploaded[key], 'relative_path': relative})
        manifest = {'created_at': now(), 'config': config, 'files': receipts,
                    'source_receipts': len((a.out / 'captures.jsonl').read_text().splitlines()) if (a.out / 'captures.jsonl').exists() else 0,
                    'completed_inputs': len(states), 'expected_inputs': len(rows),
                    'input_complete': len(states) == len(rows), 'policy_completeness': 'not_established'}
        body = (json.dumps(manifest, indent=2) + '\n').encode()
        key = root_prefix + 'checkpoints/' + digest(body) + '.json'
        receipt = bridge.put(key, body)
        write_json(a.out / 'r2-readback.json', {'checkpoint': receipt, 'files': receipts})
        print(json.dumps({'r2_checkpoint_verified': key, 'files': len(receipts), 'completed_inputs': len(states)}), flush=True)

    def acquire(row):
        state = {'candidate_id': row['candidate_id'], 'source_url': row['source_url'], 'started_at': now(),
                 'reference_sha256': row['reference_sha256'], 'parent_url': row['discovered_from']}
        try:
            if time.monotonic() >= deadline:
                state['status'] = 'not_attempted_job_time_budget'
                return state
            relation = row.get('source_role', 'campaign_policy_candidate')
            kind = 'reference_page' if relation == 'linked_reference_pdf' else row['kind']
            record, body = engine.acquire(row['candidate_id'], row['source_url'], kind, [],
                                           context='New explicit policy link from ' + row['discovered_from'] + '; parent SHA256 ' + row['reference_sha256'],
                                           require_pdf=row['kind'] == 'campaign_pdf')
            review, text = content_review.review(record, body, by_id[row['candidate_id']])
            review['source_role'] = relation
            review['reference_sha256'] = row['reference_sha256']
            write_json(a.out / 'reviews' / (digest((row['candidate_id'] + row['source_url']).encode()) + '.json'), review)
            text_path = a.out / 'text' / (record['sha256'] + '.txt')
            text_path.parent.mkdir(exist_ok=True)
            text_path.write_text(text)
            state.update(status='saved_source_bytes', sha256=record['sha256'], bytes=record['bytes'],
                         saved_policy_text_candidate=review['saved_policy_text_candidate'] and relation != 'linked_reference_pdf',
                         scope_status=review['scope_status'], source_role=relation,
                         machine_current_race_evidence=review['machine_current_race_evidence'])
        except Exception as error:
            state.update(status='blocked_or_error', reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
        finally:
            state['finished_at'] = now()
            with lock:
                states.append(state)
                write_json(a.out / 'attempts.json', states)
        return state

    # All rows for a campaign origin stay in one shard. Two local threads share
    # Engine origin locks/controls; checkpoints are flushed after each 20 URLs.
    try:
        for offset in range(0, len(rows), 20):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(acquire, rows[offset:offset + 20]))
            engine.save_controls()
            upload_checkpoint()
    finally:
        engine.save_controls()
        summary = {'finished_at': now(), 'input_sources': len(rows), 'completed_inputs': len(states),
                   'status_counts': dict(collections.Counter(r['status'] for r in states)),
                   'saved_source_responses': sum(r['status'] == 'saved_source_bytes' for r in states),
                   'saved_policy_text_candidates': sum(r.get('saved_policy_text_candidate', False) for r in states),
                   'network_requests': engine.requests, 'response_body_bytes_charged': engine.downloaded,
                   'policy_completeness': 'not_established', 'scoring_requests': 0}
        write_json(a.out / 'summary.json', summary)
        upload_checkpoint()
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
