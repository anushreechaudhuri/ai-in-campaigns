"""Read the existing bucket inventory, including every pagination cursor."""
import argparse
import hashlib
import importlib.util
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('r2_transfer', Path(__file__).with_name('r2-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument('--prefix')
    scope.add_argument('--all', action='store_true', help='Explicitly enumerate metadata for the whole existing bucket')
    p.add_argument('--wrangler-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise ValueError('Use a fresh inventory output path')
    client = transfer.R2('e84fe353569d25772e1018a57e090b26', 'ai-in-campaigns-data', a.wrangler_config)
    objects, pages, cursor, seen_cursors = {}, [], None, set()
    started = datetime.now(timezone.utc).isoformat()
    while True:
        params = {'per_page': 1000}
        if a.prefix:
            params['prefix'] = a.prefix
        if cursor:
            if cursor in seen_cursors:
                raise ValueError('Repeated inventory cursor')
            seen_cursors.add(cursor)
            params['cursor'] = cursor
        status, body = client.api('/objects?' + urllib.parse.urlencode(params))
        if status != 200:
            raise ValueError(f'R2 inventory failed, HTTP {status}')
        value = json.loads(body)
        if value.get('success') is not True or not isinstance(value.get('result'), list):
            raise ValueError('Invalid inventory envelope')
        pages.append({'response_sha256': hashlib.sha256(body).hexdigest(),
                      'objects': len(value['result']), 'result_info': value.get('result_info')})
        for item in value['result']:
            objects[item['key']] = item
        info = value.get('result_info') or {}
        if not info.get('is_truncated'):
            break
        cursor = info.get('cursor')
        if not cursor:
            raise ValueError('Truncated listing lacks a cursor')
    result = {'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
              'bucket': 'ai-in-campaigns-data', 'prefix': a.prefix or '',
              'object_count': len(objects), 'bytes': sum(x['size'] for x in objects.values()),
              'pages': pages, 'objects': sorted(objects.values(), key=lambda x: x['key']),
              'consistency_note': 'Concurrent append-only jobs can add keys while listing. This is the exact set of confirmed objects returned by all pages.'}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('object_count', 'bytes', 'started_at', 'completed_at')}))


if __name__ == '__main__':
    main()
