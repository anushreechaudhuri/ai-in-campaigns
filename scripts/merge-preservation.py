#!/usr/bin/env python3
"""Verify and merge private source captures while keeping coverage claims separate."""
import argparse
import collections
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from campaign_research.common import has_html_markup, inside, now, read_json, write_json


def merge(inventory, runs, out, scope_review=None):
    out = Path(out)
    if out.exists():
        raise ValueError("Use a new output directory to preserve prior audit snapshots")
    source_inventory = read_json(inventory)
    rows = source_inventory['candidates']
    ids = [r.get('candidate_id') for r in rows]
    if any(not isinstance(cid, str) or not cid.strip() for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError('Canonical inventory requires unique nonempty candidate IDs')
    run_ids = [run_id for run_id, _ in runs]
    if len(set(run_ids)) != len(run_ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', run_id) for run_id in run_ids):
        raise ValueError('Run IDs must be unique simple identifiers')
    for run_id, root in runs:
        root = Path(root)
        if not root.is_dir() or not any((root / marker).exists() for marker in (
                'captures.jsonl', 'checkpoints', 'attempts.json', 'verification.json', 'discovery-results.json')):
            raise ValueError('Explicit run input is missing or has no capture/checkpoint evidence: ' + run_id)
    out.mkdir(parents=True)
    candidates = {r['candidate_id']: r for r in rows}
    decisions = {}
    if scope_review:
        for item in read_json(scope_review)['decisions']:
            key = (item.get('candidate_id'), item.get('sha256'))
            if (key[0] not in candidates or not re.fullmatch(r'[a-f0-9]{64}', str(key[1]))
                    or item.get('decision') != 'unrelated_response' or not str(item.get('reason', '')).strip()
                    or key in decisions):
                raise ValueError('Invalid or duplicate scope review decision')
            decisions[key] = item
    applied_decisions = set()
    records, attempts, failures = {}, collections.defaultdict(list), []
    discovery = collections.defaultdict(list)
    input_count = 0
    for run_id, root in runs:
        root = Path(root)
        metadata = out / 'run-metadata' / run_id
        metadata.mkdir(parents=True)
        for filename in ('input-public-seed.json', 'run-config.json', 'source-note.json',
                         'verification.json', 'capture-summary.json', 'collect-summary.json', 'summary.json'):
            if (root / filename).is_symlink():
                raise ValueError('Run metadata symlink refused')
            if (root / filename).is_file():
                shutil.copyfile(inside(root, filename), metadata / filename)
        discovery_path = root / 'discovery-results.json'
        if discovery_path.is_symlink():
            raise ValueError('Discovery metadata symlink refused')
        if discovery_path.exists():
            checked_discovery = []
            for original in read_json(discovery_path):
                state = dict(original)
                if state.get('candidate_id') not in candidates:
                    raise ValueError('Discovery candidate absent from canonical inventory')
                if state.get('index_path') or state.get('index_sha256'):
                    if not state.get('index_path') or not re.fullmatch(r'[a-f0-9]{64}', str(state.get('index_sha256', ''))):
                        raise ValueError('Discovery index receipt is incomplete')
                    source = inside(root, state['index_path'])
                    body = source.read_bytes()
                    if hashlib.sha256(body).hexdigest() != state['index_sha256']:
                        raise ValueError('Discovery index checksum mismatch')
                    destination = metadata / 'discovery-objects' / (state['index_sha256'] + '.json')
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(source, destination)
                    state['index_path'] = str(destination.relative_to(out))
                state['run_id'] = run_id
                discovery[state['candidate_id']].append(state)
                checked_discovery.append(state)
            write_json(metadata / 'discovery-results.json', checked_discovery)
        for checkpoint in sorted((root / 'checkpoints').glob('*.json')):
            state = read_json(checkpoint)
            if state.get('candidate_id') not in candidates:
                raise ValueError('Checkpoint candidate absent from canonical inventory')
            attempts[state['candidate_id']].append({
                'run_id': run_id, **{k: state.get(k) for k in (
                    'status', 'started_at', 'finished_at', 'errors', 'resolved_website',
                    'website_evidence', 'policy_pages', 'policy_pdf_candidates', 'scope_complete')}})
        if (root / 'attempts.json').exists():
            for state in read_json(root / 'attempts.json'):
                if state.get('candidate_id') not in candidates:
                    raise ValueError('Attempt candidate absent from canonical inventory')
                attempts[state['candidate_id']].append({'run_id': run_id, **state})
        manifest = root / 'captures.jsonl'
        if not manifest.exists():
            continue
        for line in manifest.read_text().splitlines():
            record = json.loads(line)
            if record.get('candidate_id') not in candidates:
                raise ValueError('Captured candidate absent from canonical inventory')
            if record.get('status') != 'captured':
                failures.append({'run_id': run_id, **record})
                attempts[record['candidate_id']].append({'run_id': run_id, **record})
                continue
            input_count += 1
            path = inside(root, record['path'])
            body = path.read_bytes()
            sha = hashlib.sha256(body).hexdigest()
            if sha != record['sha256'] or len(body) != record['bytes']:
                raise ValueError('Source body checksum/length mismatch')
            record['source_content_valid'] = (has_html_markup(body) if record['kind'] == 'campaign_page'
                else body.startswith(b'%PDF-') if record['kind'] in ('campaign_pdf', 'policy_pdf') else bool(body))
            review_key = (record['candidate_id'], sha)
            if record.get('source_scope_excluded') or record.get('scope_review'):
                inherited = record.get('scope_review')
                if (not isinstance(inherited, dict) or inherited.get('candidate_id') != review_key[0]
                        or inherited.get('sha256') != review_key[1]
                        or inherited.get('decision') != 'unrelated_response'
                        or not str(inherited.get('reason', '')).strip()):
                    raise ValueError('Inherited scope exclusion requires a matching checksum-bound review')
                decisions.setdefault(review_key, inherited)
            record['source_scope_excluded'] = review_key in decisions
            if review_key in decisions:
                record['scope_review'] = decisions[review_key]
                applied_decisions.add(review_key)
            if not record['source_content_valid']:
                record['content_validation_error'] = 'Response bytes do not match declared campaign HTML/PDF content'
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
    if applied_decisions != set(decisions):
        raise ValueError('Scope review decision does not match any preserved response')
    values = sorted(records.values(), key=lambda r: (r['candidate_id'], r['source_url'], r['sha256']))
    # A review in a later input applies to all identical preserved bytes, including
    # an earlier deduplicated receipt from a source run that predates the review.
    for record in values:
        review_key = (record['candidate_id'], record['sha256'])
        record['source_scope_excluded'] = review_key in decisions
        if review_key in decisions:
            record['scope_review'] = decisions[review_key]
    if decisions:
        write_json(out / 'scope-review.json', {
            'input_sha256': hashlib.sha256(Path(scope_review).read_bytes()).hexdigest() if scope_review else None,
            'decisions': list(decisions.values()), 'inherited_exclusions_preserved': True})
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
        campaign = [r for r in captured if r['kind'] in ('campaign_page', 'campaign_pdf', 'policy_pdf') and r['source_content_valid'] and not r['source_scope_excluded']]
        reference = [r for r in captured if r['kind'] not in ('campaign_page', 'campaign_pdf', 'policy_pdf')]
        history = attempts[cid]
        context_receipts = [r for r in campaign if any(o.get('policy_context') for o in r['observations'])]
        ledger.append({**candidate, 'verified_response_count': len(captured),
                       'campaign_response_count': len(campaign), 'reference_response_count': len(reference),
                       'unusable_response_count': sum(not r['source_content_valid'] for r in captured),
                       'scope_excluded_response_count': sum(r['source_scope_excluded'] for r in captured),
                       'distinct_source_urls': len({r['source_url'] for r in captured}),
                       'pdf_candidate_count': sum(r['path'].endswith('.pdf') for r in campaign),
                       'reference_pdf_count': sum(r['path'].endswith('.pdf') for r in reference),
                       'unreviewed_source_context_receipts': len(context_receipts),
                       'source_bytes_preserved': bool(campaign), 'reference_bytes_preserved': bool(reference),
                       'current_candidate_site_identity': 'requires_review',
                       'policy_content_completeness': 'not_established',
                       'attempts': history,
                       'discovery_attempts': discovery[cid],
                       'discovery_access_restricted': any('robots.txt HTTP 202' in str(a.get('errors', [])) for a in history),
                       'coverage_note': 'Captured bytes do not prove current campaign identity, policy completeness, or AI authorship.'})
    write_json(out / 'coverage-ledger.json', {'created_at': now(),
               'inventory_sha256': hashlib.sha256(Path(inventory).read_bytes()).hexdigest(),
               'inventory_provenance': source_inventory.get('provenance', {}),
               'status_note': 'Supplied election statuses are not refreshed by capture; source file modified time is not a status verification date.',
               'candidates': ledger})
    write_json(out / 'pdf-verification.json', pdf_checks)
    objects = list((out / 'objects').glob('*')) if (out / 'objects').exists() else []
    unusable = [r for r in values if not r['source_content_valid']]
    write_json(out / 'unusable-response-receipts.json', unusable)
    write_json(out / 'scope-excluded-receipts.json', [r for r in values if r['source_scope_excluded']])
    campaign = [r for r in values if r['kind'] in ('campaign_page', 'campaign_pdf', 'policy_pdf') and r['source_content_valid'] and not r['source_scope_excluded']]
    campaign_ids = {r['candidate_id'] for r in campaign}
    campaign_paths = {r['path'] for r in campaign}
    summary = {'created_at': now(), 'canonical_candidates': len(candidates),
               'input_success_receipts': input_count, 'deduplicated_receipts': len(values),
               'candidate_ids_with_captured_bytes': len(campaign_ids),
               'candidate_ids_without_captured_bytes': len(candidates) - len(campaign_ids),
               'coverage_counting_basis': 'Campaign page/PDF source bytes only; reference pages and detector reports excluded',
               'unusable_response_receipts': len(unusable),
               'scope_excluded_response_receipts': sum(r['source_scope_excluded'] for r in values),
               'unique_bodies': len(objects), 'unique_body_bytes': sum(p.stat().st_size for p in objects),
               'unique_usable_campaign_body_bytes': sum((out / p).stat().st_size for p in campaign_paths),
               'discovery_attempts': sum(map(len, discovery.values())),
               'discovery_statuses': dict(collections.Counter(r.get('status', 'unspecified') for history in discovery.values() for r in history)),
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
    parser.add_argument('--scope-review', help='Private checksum-bound review decisions excluding unrelated responses')
    args = parser.parse_args()
    print(json.dumps(merge(args.inventory, [r.split('=', 1) for r in args.run], args.out, args.scope_review)))
