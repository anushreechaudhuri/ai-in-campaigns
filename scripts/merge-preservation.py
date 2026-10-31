#!/usr/bin/env python3
"""Verify and merge private source captures while keeping coverage claims separate."""
import argparse
import collections
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from campaign_research.common import inside, now, read_json, write_json


def merge(inventory, runs, out):
    out = Path(out)
    if out.exists():
        raise ValueError("Use a new output directory to preserve prior audit snapshots")
    out.mkdir(parents=True)
    candidates = {r['candidate_id']: r for r in read_json(inventory)['candidates']}
    records, attempts, failures = {}, collections.defaultdict(list), []
    input_count = 0
    for run_id, root in runs:
        root = Path(root)
        for checkpoint in sorted((root / 'checkpoints').glob('*.json')):
            state = read_json(checkpoint)
            attempts[state['candidate_id']].append({
                'run_id': run_id, **{k: state.get(k) for k in (
                    'status', 'started_at', 'finished_at', 'errors', 'resolved_website',
                    'website_evidence', 'policy_pages', 'policy_pdf_candidates', 'scope_complete')}})
        if (root / 'attempts.json').exists():
            for state in read_json(root / 'attempts.json'):
                attempts[state['candidate_id']].append({'run_id': run_id, **state})
        manifest = root / 'captures.jsonl'
        if not manifest.exists():
            continue
        for line in manifest.read_text().splitlines():
            record = json.loads(line)
            if record.get('status') != 'captured':
                failures.append({'run_id': run_id, **record})
                attempts[record['candidate_id']].append({'run_id': run_id, **record})
                continue
            input_count += 1
            if record['candidate_id'] not in candidates:
                raise ValueError('Captured candidate absent from canonical inventory')
            path = inside(root, record['path'])
            body = path.read_bytes()
            sha = hashlib.sha256(body).hexdigest()
            if sha != record['sha256'] or len(body) != record['bytes']:
                raise ValueError('Source body checksum/length mismatch')
            extension = '.pdf' if body.startswith(b'%PDF-') else '.html'
            relative = 'objects/' + sha + extension
            target = out / relative
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copyfile(path, target)
            record['path'] = relative
            for check in record.get('robots_checks', []):
                robot = inside(root, check['path'])
                if hashlib.sha256(robot.read_bytes()).hexdigest() != check['sha256']:
                    raise ValueError('Robots receipt checksum mismatch')
                destination = out / 'robots' / (check['sha256'] + '.txt')
                destination.parent.mkdir(exist_ok=True)
                shutil.copyfile(robot, destination)
                check['path'] = str(destination.relative_to(out))
            key = (record['candidate_id'], record['source_url'], sha, record['kind'])
            if key not in records:
                records[key] = {**record, 'observations': []}
            observation = {'run_id': run_id, 'captured_at': record.get('captured_at'),
                           'final_url': record.get('final_url'),
                           'policy_context': record.get('policy_context'),
                           'robots_checks': record.get('robots_checks', []),
                           'redirects': record.get('redirects', [])}
            if observation not in records[key]['observations']:
                records[key]['observations'].append(observation)
    values = sorted(records.values(), key=lambda r: (r['candidate_id'], r['source_url'], r['sha256']))
    (out / 'captures.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in values))
    write_json(out / 'failed-captures.json', failures)
    by_candidate = collections.defaultdict(list)
    pdf_checks = []
    for record in values:
        by_candidate[record['candidate_id']].append(record)
        if record['path'].endswith('.pdf'):
            body = (out / record['path']).read_bytes()
            check = {'candidate_id': record['candidate_id'], 'source_url': record['source_url'],
                     'sha256': record['sha256'], 'pdf_signature': body.startswith(b'%PDF-'),
                     'eof_marker': b'%%EOF' in body[-2048:], 'policy_semantics': 'unreviewed'}
            if shutil.which('pdfinfo'):
                result = subprocess.run(['pdfinfo', str(out / record['path'])], capture_output=True, timeout=30)
                check['pdfinfo_exit_code'] = result.returncode
                check['pages'] = next((line.split(':', 1)[1].strip() for line in result.stdout.decode(errors='replace').splitlines() if line.startswith('Pages:')), None)
            pdf_checks.append(check)
    ledger = []
    for cid, candidate in candidates.items():
        captured = by_candidate[cid]
        history = attempts[cid]
        context_receipts = [r for r in captured if any(o.get('policy_context') for o in r['observations'])]
        ledger.append({**candidate, 'verified_response_count': len(captured),
                       'distinct_source_urls': len({r['source_url'] for r in captured}),
                       'pdf_candidate_count': sum(r['path'].endswith('.pdf') for r in captured),
                       'unreviewed_source_context_receipts': len(context_receipts),
                       'source_bytes_preserved': bool(captured),
                       'current_candidate_site_identity': 'requires_review',
                       'policy_content_completeness': 'not_established',
                       'attempts': history,
                       'discovery_access_restricted': any('robots.txt HTTP 202' in str(a.get('errors', [])) for a in history),
                       'coverage_note': 'Captured bytes do not prove current campaign identity, policy completeness, or AI authorship.'})
    write_json(out / 'coverage-ledger.json', {'created_at': now(), 'candidates': ledger})
    write_json(out / 'pdf-verification.json', pdf_checks)
    objects = list((out / 'objects').glob('*')) if (out / 'objects').exists() else []
    summary = {'created_at': now(), 'canonical_candidates': len(candidates),
               'input_success_receipts': input_count, 'deduplicated_receipts': len(values),
               'candidate_ids_with_captured_bytes': len({r['candidate_id'] for r in values}),
               'candidate_ids_without_captured_bytes': len(candidates) - len({r['candidate_id'] for r in values}),
               'unique_bodies': len(objects), 'unique_body_bytes': sum(p.stat().st_size for p in objects),
               'response_kinds': dict(collections.Counter(r['kind'] for r in values)),
               'scope_complete': False, 'runs': [r[0] for r in runs]}
    write_json(out / 'merge-summary.json', summary)
    write_json(out / 'sha256sums.json', [{'path': str(p.relative_to(out)), 'bytes': p.stat().st_size,
                                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                       for p in sorted(out.rglob('*')) if p.is_file()])
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--run', action='append', required=True, help='RUN_ID=CAPTURE_DIRECTORY')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps(merge(args.inventory, [r.split('=', 1) for r in args.run], args.out)))
