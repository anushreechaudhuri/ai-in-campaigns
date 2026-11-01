"""Offline policy-text triage. Signals and machine evidence never imply completeness."""
import argparse
import collections
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

TOPICS = re.compile(r'\b(?:health(?:care)?|education|immigration|climate|energy|tax(?:es)?|housing|abortion|veterans|economy|jobs|agriculture|security|rights|infrastructure|trade|medicare|social security)\b', re.I)
POLICY = re.compile(r'\b(?:issues?|polic(?:y|ies)|priorities|platform|agenda|vision|positions?|plans?)\b', re.I)
PARKED = re.compile(r'(?:domain (?:is )?for sale|buy this domain|sedo domain parking|this domain has expired)', re.I)
NON_POLICY = re.compile(r'privacy|cookie|terms[-_/ ]|donat(?:e|ion)|unsubscribe|merchant-policies', re.I)


class VisibleText(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.hidden, self.parts = [], []
        # Removing unexecuted script/style payloads makes very large app shells cheap.
        body = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', body, flags=re.I | re.S)
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer', 'svg', 'noscript', 'template'):
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]
        if not self.hidden and tag in ('p', 'h1', 'h2', 'h3', 'li', 'section', 'div'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return '\n'.join(line for line in (re.sub(r'\s+', ' ', s).strip() for s in ''.join(self.parts).splitlines()) if line)


def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()).strip()


def review(record, body, candidate, pdf_extractor=True):
    if hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('Source hash mismatch')
    is_pdf = body.startswith(b'%PDF-')
    if is_pdf and pdf_extractor:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.pdf'
            path.write_bytes(body)
            result = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True, timeout=45)
            text = result.stdout.decode('utf-8', 'replace') if result.returncode == 0 else ''
    elif is_pdf:
        text = ''
    else:
        encoding = 'utf-16' if body.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = VisibleText(body.decode(encoding, 'replace')).text()
    words = len(re.findall(r"\b[\w'-]+\b", text))
    topics = sorted({x.lower() for x in TOPICS.findall(text)})
    norm = ' ' + normalize(text) + ' '
    name = normalize(candidate['name'])
    tokens = [x for x in name.split() if len(x) > 1 and x not in ('iii', 'ii', 'iv')]
    name_match = bool(tokens and all(re.search(r'\b' + re.escape(x) + r'\b', norm) for x in (tokens[0], tokens[-1])))
    office = bool(re.search(r'\bsenat(?:e|or)\b' if candidate['chamber'] == 'Senate' else r'\b(?:congress|house of representatives|congressional)\b', norm))
    cycle = bool(re.search(r'\b2026\b', text))
    parked = bool(PARKED.search(text))
    path = urllib.parse.urlsplit(record.get('final_url') or record['source_url']).path
    non_policy = bool(NON_POLICY.search(path))
    url_signal = bool(POLICY.search(path)) and not non_policy
    content_signal = words >= 150 and len(topics) >= 2 and not non_policy
    result = {'candidate_id': record['candidate_id'], 'source_url': record['source_url'],
              'source_sha256': record['sha256'], 'captured_at': record.get('captured_at'),
              'text_words': words, 'policy_url_signal': url_signal,
              'non_policy_navigation_url': non_policy,
              'policy_text_signal': content_signal, 'topic_terms': topics,
              'candidate_name_text_match': name_match, 'office_text_match': office,
              'cycle_2026_text_present': cycle, 'parked_domain_text': parked,
              'machine_current_race_evidence': name_match and office and cycle and not parked,
              'scope_status': 'excluded_parked_response' if parked else 'requires_review',
              'saved_policy_text_candidate': content_signal and not parked and (url_signal or is_pdf),
              'document_kind': 'unreviewed_candidate_linked_pdf' if is_pdf else 'html',
              'policy_completeness': 'not_established',
              'limitations': 'Word/topic/identity matches are reproducible triage evidence, not a verified position, PDF attribution, or complete platform.'}
    return result, text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(a.inventory.read_text())['candidates']
    by_id = {c['candidate_id']: c for c in inventory}
    results, errors, cache = [], [], {}
    for line in (a.corpus / 'captures.jsonl').read_text().splitlines():
        row = json.loads(line)
        if row.get('source_scope_excluded') or row['kind'] not in ('campaign_page', 'campaign_pdf', 'policy_pdf'):
            continue
        try:
            key = (row['candidate_id'], row['sha256'])
            if key in cache:
                result = {**cache[key], 'source_url': row['source_url'], 'captured_at': row.get('captured_at')}
                # URL-specific signal must be recomputed for byte-identical home/issues shells.
                path = urllib.parse.urlsplit(row.get('final_url') or row['source_url']).path
                result['non_policy_navigation_url'] = bool(NON_POLICY.search(path))
                result['policy_url_signal'] = bool(POLICY.search(path)) and not result['non_policy_navigation_url']
                result['policy_text_signal'] = result['text_words'] >= 150 and len(result['topic_terms']) >= 2 and not result['non_policy_navigation_url']
                result['saved_policy_text_candidate'] = result['policy_text_signal'] and not result['parked_domain_text'] and (result['policy_url_signal'] or result['document_kind'] != 'html')
            else:
                result, text = review(row, (a.corpus / row['path']).read_bytes(), by_id[row['candidate_id']])
                cache[key] = result
                dest = a.out / 'text' / (row['sha256'] + '.txt')
                dest.parent.mkdir(exist_ok=True)
                dest.write_text(text)
                result['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            results.append(result)
        except Exception as error:
            errors.append({'candidate_id': row['candidate_id'], 'source_sha256': row['sha256'], 'error_type': type(error).__name__})
        if len(results) % 500 == 0:
            print(json.dumps({'reviewed_receipts': len(results)}), flush=True)
    (a.out / 'policy-content-reviews.json').write_text(json.dumps(results, indent=2) + '\n')
    (a.out / 'errors.json').write_text(json.dumps(errors, indent=2) + '\n')
    fields = ['policy_url_signal', 'policy_text_signal', 'saved_policy_text_candidate', 'machine_current_race_evidence', 'parked_domain_text']
    summary = {'reviewed_receipts': len(results), 'review_errors': len(errors),
               'candidates': len({r['candidate_id'] for r in results}),
               'candidate_counts': {key: len({r['candidate_id'] for r in results if r[key]}) for key in fields},
               'reviewed_policy_pdf_count': 0,
               'note': 'Saved policy-text candidates and identity evidence are machine triage. Completeness and PDF attribution remain unestablished.'}
    (a.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
