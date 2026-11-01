"""Discover explicit policy links from matched public directory profiles in a cloud job.

Directory summaries remain reference material. Only candidate-site response bytes
enter campaign coverage, with current-race identity and policy attribution review.
"""
import argparse
import collections
import importlib.util
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from campaign_research.collect import Engine, Anchors, POLICY, EXCLUDE, campaign_url, process_candidate, validate_controls
from campaign_research.common import atomic_write, digest, now, write_json

spec = importlib.util.spec_from_file_location('cloud_batch', Path(__file__).with_name('cloud-policy-batch.py'))
cloud_batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud_batch)
STATE_NAMES = dict(part.split(':', 1) for part in 'AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming|DC:District of Columbia|AS:American Samoa|GU:Guam|MP:Northern Mariana Islands|PR:Puerto Rico|VI:Virgin Islands'.split('|'))


def normalize(text):
    return re.sub('[^a-z0-9]', '', unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower())


def normalized_url(url):
    p = urllib.parse.urlsplit(campaign_url(url))
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                                   if k.lower() not in ('fbclid', 'gclid') and not k.lower().startswith('utm_')])
    return ((p.hostname or '').lower().removeprefix('www.'),
            urllib.parse.unquote(p.path).rstrip('/') or '/', query)


class Facts(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.tag, self.words, self.links, self.fields, self.heading, self.label = None, [], [], {}, '', ''
        self.feed(body.decode('utf-8', 'replace'))

    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'dt', 'dd'):
            self.tag, self.words, self.links = tag, [], []
        elif self.tag and tag == 'a' and dict(attrs).get('href'):
            self.links.append(dict(attrs)['href'])

    def handle_data(self, text):
        if self.tag:
            self.words.append(text)

    def handle_endtag(self, tag):
        if tag != self.tag:
            return
        text = re.sub(r'\s+', ' ', ''.join(self.words)).strip()
        if tag == 'h1':
            self.heading = text
        elif tag == 'dt':
            self.label = text.lower()
        else:
            self.fields[self.label] = {'text': text, 'links': self.links.copy()}
        self.tag = None


def validate_profile(body, profile, candidate):
    facts = Facts(body)
    if normalize(facts.heading) != normalize(profile['directory_name']):
        raise ValueError('Directory profile name differs from matched index identity')
    field = lambda key: facts.fields.get(key, {}).get('text', '')
    if field('fec candidate id').upper() != profile['fec_candidate_id']:
        raise ValueError('Directory FEC identity mismatch')
    if field('election year') != '2026':
        raise ValueError('Directory profile does not establish the2026cycle')
    office = field('office sought')
    office_pattern = r'\bU\.S\.\s+' + candidate['chamber'] + r'\b'
    if candidate['chamber'] == 'House' and candidate['state'] in ('DC', 'AS', 'GU', 'MP', 'PR', 'VI'):
        office_pattern += r'|\b(?:U\.S\.\s+Delegate|Resident Commissioner)\b'
    if not re.search(office_pattern, office):
        raise ValueError('Directory federal office mismatch')
    if not re.search(r'\b' + re.escape(STATE_NAMES.get(candidate['state'], candidate['state'])) + r'\b', office, re.I):
        raise ValueError('Directory visible state label mismatch')
    if candidate['chamber'] == 'House':
        match = re.search(r'District\s+(\d+)', office)
        expected = str(candidate['district'])
        expected = '0' if expected.lower() in ('', 'at-large', 'at large', 'al', '00', '0') else str(int(expected))
        if not (expected == '0' and re.search(r'at.large', office, re.I)) and (not match or str(int(match[1])) != expected):
            raise ValueError('Directory district mismatch')
    # The index route and exact recorded FEC ID both bind the state. Retain the
    # visible office label as review evidence rather than guessing postal text.
    if urllib.parse.urlsplit(profile['directory_url']).path.split('/')[2].upper() != candidate['state']:
        raise ValueError('Directory state mismatch')
    websites = facts.fields.get('website', {}).get('links', [])
    sites = [campaign_url(url) for url in websites if urllib.parse.urlsplit(campaign_url(url)).scheme == 'https']
    if len(sites) != 1:
        return {'website': None, 'reason': 'No unique explicit campaign website in directory profile', 'office_label': office}, []
    site = sites[0]
    host = normalized_url(site)[0]
    if host.endswith(('.gov', '.edu')) or host in ('facebook.com', 'twitter.com', 'x.com', 'instagram.com', 'youtube.com', 'ballotpedia.org', 'web.archive.org'):
        raise ValueError('Directory website is institutional, social or reference-only')
    policy_links = []
    for href, label in Anchors(body).links:
        url = urllib.parse.urljoin(profile['directory_url'], href).split('#', 1)[0]
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or normalized_url(url)[0] != host:
            continue
        if EXCLUDE.search(label + ' ' + p.path):
            continue
        if POLICY.search(label + ' ' + p.path) or 'campaign website' in label.lower():
            policy_links.append(campaign_url(url))
    return {'website': site, 'office_label': office,
            'identity_basis': profile['identity_join_method'], 'directory_fec_id': profile['fec_candidate_id'],
            'current_campaign_identity': 'Candidate website still requires saved-body review'}, list(dict.fromkeys(policy_links))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bridge', 'input-key', 'input-sha256', 'write-prefix'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--fallback-input', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-seconds', type=int, default=6000)
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    if not 1 <= a.max_seconds <= 12600:
        raise ValueError('Use at most3.5hours of source acquisition')
    a.out.mkdir(parents=True, exist_ok=a.resume)
    bridge = cloud_batch.Bridge(a.bridge)
    try:
        raw = bridge.get(a.input_key)
    except (RuntimeError, urllib.error.URLError, OSError):
        raw, bridge = a.fallback_input.read_bytes(), None
    if digest(raw) != a.input_sha256:
        raise ValueError('Directory input hash mismatch')
    payload = json.loads(raw)
    if a.resume and (a.out / 'input.json').exists():
        if json.loads((a.out / 'input.json').read_text()) != payload:
            raise ValueError('Resume input differs from original private input')
    else:
        write_json(a.out / 'input.json', payload)
    profiles = payload['profiles']
    candidates = {c['candidate_id']: c for c in payload['candidates']}
    controls = validate_controls(payload['blocked_source_origins'])
    seen = {(cid, normalized_url(url)) for cid, urls in payload['seen_urls'].items() for url in urls}
    prior_records = [json.loads(line) for line in (a.out / 'captures.jsonl').read_text().splitlines()] if a.resume and (a.out / 'captures.jsonl').exists() else []
    for row in prior_records:
        body = (a.out / row['path']).read_bytes()
        if digest(body) != row['sha256'] or len(body) != row['bytes']:
            raise ValueError('Resume source bytes fail verification')
        if row['kind'] != 'reference_page':
            for url in (row['source_url'], row.get('final_url')):
                if url:
                    seen.add((row['candidate_id'], normalized_url(url)))
    lock = threading.Lock()
    captured = [r for r in prior_records if r['kind'] in ('campaign_page', 'campaign_pdf', 'policy_pdf')]
    states = [json.loads(line) for line in (a.out / 'progress.jsonl').read_text().splitlines()] if a.resume and (a.out / 'progress.jsonl').exists() else []
    completed_ids = {state['candidate_id'] for state in states}
    if len(completed_ids) != len(states) or [s['candidate_id'] for s in states] != [p['candidate_id'] for p in profiles[:len(states)]]:
        raise ValueError('Resume progress is not an exact ordered prefix of the bound input')
    for state in states:
        for error in state.get('capture_errors', []) + state.get('explicit_source_errors', []):
            if isinstance(error, dict) and error.get('url'):
                seen.add((state['candidate_id'], normalized_url(error['url'])))
    prior_summary = json.loads((a.out / 'summary.json').read_text()) if a.resume and (a.out / 'summary.json').exists() else {}
    prior_bytes = prior_summary.get('lifetime_body_bytes_charged', 0)
    prior_requests = prior_summary.get('lifetime_network_requests', 0)
    if a.resume and (a.out / 'transport-progress.json').exists():
        transport_progress = json.loads((a.out / 'transport-progress.json').read_text())
        prior_bytes = max(prior_bytes, transport_progress['lifetime_body_bytes_charged'])
        prior_requests = max(prior_requests, transport_progress['lifetime_network_requests'])

    class FreshEngine(Engine):
        def capture(self, row):
            key = (row['candidate_id'], normalized_url(row['source_url']))
            if row['kind'] != 'reference_page':
                with lock:
                    if key in seen:
                        raise ValueError('Previously saved/attempted or reserved URL; no new request')
                    seen.add(key)
            record, links = super().capture(row)
            if row['kind'] != 'reference_page':
                captured.append(record)
            return record, links

    engine = FreshEngine(a.out, min(768 * 1024 * 1024, max(1, 2 * 1024 ** 3 - prior_bytes)), max(1, 16000 - prior_requests))
    if prior_bytes >= 2 * 1024 ** 3 or prior_requests >= 16000:
        engine.exhausted = True
    if a.resume and (a.out / 'source-controls.json').exists():
        saved_controls = json.loads((a.out / 'source-controls.json').read_text())
        controls.update(validate_controls(saved_controls['paused_origins']))
        engine.control_events = saved_controls['events']
    for url, reason in controls.items():
        engine.pause_origin(url, reason, inherited=True)
    engine.save_controls()
    config = {'started_at': now(), 'input_sha256': digest(raw), 'workflow_commit': os.environ['GITHUB_SHA'],
              'code_commit': os.environ.get('CODE_COMMIT'), 'max_seconds': a.max_seconds,
              'max_body_bytes': engine.max_bytes, 'max_requests': engine.max_requests,
              'profile_inputs': len(profiles), 'scoring_requests': 0}
    if a.resume and (a.out / 'run-config.json').exists():
        saved = json.loads((a.out / 'run-config.json').read_text())
        for key in ('input_sha256', 'code_commit', 'workflow_commit'):
            if saved[key] != config[key]:
                raise ValueError('Resume code or workflow identity differs')
        config = saved
    else:
        write_json(a.out / 'run-config.json', config)
    engine.identity = digest(json.dumps(config, sort_keys=True).encode())
    deadline = time.monotonic() + a.max_seconds
    upstream = engine.upstream
    reserved_hosts = set(payload.get('reserved_campaign_hosts', []))

    def bounded(url, **kwargs):
        if time.monotonic() >= deadline:
            raise ValueError('Directory job time budget reached; no more source requests')
        if normalized_url(url)[0] in reserved_hosts:
            raise ValueError('Origin reserved for active disjoint policy batch; no concurrent request')
        charged = 0
        try:
            response = upstream(url, **kwargs)
            charged = len(response[2])
            return response
        except Exception as error:
            charged = getattr(error, 'bytes_read', 0)
            raise
        finally:
            write_json(a.out / 'transport-progress.json', {
                'checked_at': now(), 'lifetime_network_requests': prior_requests + engine.requests,
                'lifetime_body_bytes_charged': prior_bytes + engine.downloaded + charged})

    engine.upstream = bounded
    uploaded = {}
    if a.resume and (a.out / 'r2-readback.json').exists():
        saved = json.loads((a.out / 'r2-readback.json').read_text())
        for receipt in saved.get('files', []):
            path = a.out / receipt['relative_path']
            if path.is_file() and digest(path.read_bytes()) == receipt['sha256']:
                uploaded[receipt['key']] = {k: v for k, v in receipt.items() if k != 'relative_path'}
    prefix = a.write_prefix + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '/directory/'

    def checkpoint():
        nonlocal bridge
        engine.save_controls()
        write_json(a.out / 'directory-outcomes.json', states)
        write_json(a.out / 'summary.json', {
            'updated_at': now(), 'intended_profiles': len(profiles), 'completed_profiles': len(states),
            'status_counts': dict(collections.Counter(x['status'] for x in states)),
            'campaign_responses_saved': len(captured), 'network_requests': engine.requests,
            'response_body_bytes_charged': engine.downloaded, 'scoring_requests': 0,
            'lifetime_network_requests': prior_requests + engine.requests,
            'lifetime_body_bytes_charged': prior_bytes + engine.downloaded,
            'r2_enabled': bridge is not None, 'policy_completeness': 'not_established'})
        if bridge is None:
            return
        try:
            files = []
            for path in sorted(a.out.rglob('*')):
                if not path.is_file() or '.partial-' in path.name or path.name == 'r2-readback.json':
                    continue
                body = path.read_bytes()
                key = prefix + 'files/' + digest(body) + '-' + str(path.relative_to(a.out)).replace('/', '--')
                if key not in uploaded:
                    uploaded[key] = bridge.put(key, body)
                files.append({**uploaded[key], 'relative_path': str(path.relative_to(a.out))})
            marker = {'created_at': now(), 'completed_profiles': len(states), 'expected_profiles': len(profiles),
                      'input_complete': len(states) == len(profiles), 'campaign_source_receipts': len(captured),
                      'files': files, 'config': config, 'policy_completeness': 'not_established'}
            body = (json.dumps(marker, indent=2) + '\n').encode()
            receipt = bridge.put(prefix + 'checkpoints/' + digest(body) + '.json', body)
            write_json(a.out / 'r2-readback.json', {'checkpoint': receipt, 'files': files})
        except (RuntimeError, urllib.error.URLError, OSError):
            bridge = None
            write_json(a.out / 'r2-transfer-deferred.json', {'checked_at': now(), 'source_bytes_preserved': True,
                                                          'message': 'Continue capture to independent private hosted backup.'})
        print(json.dumps({'completed_profiles': len(states), 'campaign_responses_saved': len(captured),
                          'r2_enabled': bridge is not None}), flush=True)

    try:
        for profile in profiles:
            cid = profile['candidate_id']
            if cid in completed_ids:
                continue
            if time.monotonic() >= deadline or engine.exhausted:
                break
            state = {'candidate_id': cid, 'directory_url': profile['directory_url'], 'started_at': now(),
                     'identity_join_method': profile['identity_join_method'], 'scope_complete': False}
            start_count = len(captured)
            try:
                cached = payload.get('cached_reference_profiles', {}).get(profile['directory_url'])
                if cached:
                    record, body = cached['record'], cached['body_utf8'].encode()
                    if digest(body) != record['sha256']:
                        raise ValueError('Cached reference profile checksum mismatch')
                    atomic_write(a.out / record['path'], body)
                    for check in record.get('robots_checks', []):
                        # Cache receipt remains linked to the original preserved probe;
                        # future crawls independently check current robots at transport.
                        check['original_probe_only'] = True
                    state['reference_reused_from_verified_probe'] = True
                else:
                    record, body = engine.acquire(cid, profile['directory_url'], 'reference_page', [],
                                                   context='New matched public directory profile; not campaign text')
                identity, links = validate_profile(body, profile, candidates[cid])
                state.update(directory_identity=identity, reference_sha256=record['sha256'])
                if not identity['website']:
                    state['status'] = 'directory_no_explicit_campaign_website'
                else:
                    starts = [url for url in links + [identity['website']] if (cid, normalized_url(url)) not in seen]
                    if not starts:
                        state['status'] = 'all_explicit_sources_previously_saved_attempted_or_reserved'
                    else:
                        # Start with the strongest explicit policy citation. A bounded
                        # same-origin expansion can discover further policy pages/PDFs.
                        result = process_candidate(engine, {**candidates[cid], 'website': starts[0]}, 12, 10)
                        for explicit in starts[1:8]:
                            if time.monotonic() >= deadline or engine.exhausted:
                                break
                            if (cid, normalized_url(explicit)) in seen:
                                continue
                            try:
                                engine.acquire(cid, explicit, 'campaign_page', [],
                                               context='Explicit campaign citation from ' + profile['directory_url'] + '; SHA256 ' + record['sha256'])
                            except Exception as error:
                                state.setdefault('explicit_source_errors', []).append({'url': explicit, 'error_type': type(error).__name__,
                                                                                    'reason': str(error) if isinstance(error, ValueError) else ''})
                        state['capture_status'] = result['status']
                        state['capture_errors'] = result['errors']
                        state['status'] = 'new_campaign_response_bytes_saved' if len(captured) > start_count else 'campaign_capture_blocked_or_no_new_source'
                for source in captured[start_count:]:
                    review, text = cloud_batch.content_review.review(source, (a.out / source['path']).read_bytes(), candidates[cid])
                    write_json(a.out / 'reviews' / (digest((cid + source['source_url']).encode()) + '.json'), review)
                    atomic_write(a.out / 'text' / (source['sha256'] + '.txt'), text.encode())
                state['new_campaign_responses'] = len(captured) - start_count
            except Exception as error:
                state.update(status='directory_or_identity_blocked_or_error',
                             reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
            state['finished_at'] = now()
            states.append(state)
            atomic_write(a.out / 'progress.jsonl', ''.join(json.dumps(s) + '\n' for s in states).encode())
            if len(states) % 20 == 0:
                checkpoint()
    finally:
        checkpoint()
        remaining = [p['candidate_id'] for p in profiles[len(states):]]
        write_json(a.out / 'not-attempted.json', {'candidate_ids': remaining,
                                                'reason': 'Bounded run stopped on time or byte/request budget'})
        # Preserve the final remaining-work file before the durable backup step.
        checkpoint()


if __name__ == '__main__':
    main()
