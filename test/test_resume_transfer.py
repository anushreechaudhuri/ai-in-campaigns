import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('r2_transfer', Path(__file__).resolve().parents[1] / 'scripts/r2-transfer.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TransferPlanTests(unittest.TestCase):
    def test_hashes_paths_bucket_and_marker_are_bound_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'capture.zip').write_bytes(b'verified archive')
            plan = {'account_id': 'e84fe353569d25772e1018a57e090b26', 'bucket': 'ai-in-campaigns-data',
                    'proposed_new_prefix': 'private/new/', 'completion_marker_key': 'private/new/COMPLETE.json',
                    'objects': [{'filename': 'capture.zip', 'key': 'private/new/capture.zip',
                                 'bytes': 16, 'sha256': module.sha(b'verified archive')}]}
            module.validate_plan(plan, root)
            for change in ({'bucket': 'other'}, {'completion_marker_key': 'private/new/../outside'},
                           {'completion_marker_key': 'private/new/%2e%2e/outside'},
                           {'completion_marker_key': 'public/COMPLETE.json'}):
                with self.assertRaises(ValueError):
                    module.validate_plan({**plan, **change}, root)
            broken = copy.deepcopy(plan)
            broken['objects'][0]['sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                module.validate_plan(broken, root)

    def test_uncertain_write_is_read_back_before_any_repeat(self):
        class Fake(module.R2):
            def __init__(self):
                self.body, self.writes = None, 0

            def request(self, key, body=None):
                if body is None:
                    return (200, self.body) if self.body is not None else (404, b'')
                self.writes += 1
                self.body = body
                raise ConnectionError('Interrupted after successful write')

        client = Fake()
        old_sleep = module.time.sleep
        try:
            module.time.sleep = lambda seconds: None
            self.assertEqual(client.ensure('private/new/raw', b'raw')['sha256'], module.sha(b'raw'))
            self.assertEqual(client.writes, 1)
            with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
                client.ensure('private/new/raw', b'different')
            self.assertEqual(client.writes, 1)
        finally:
            module.time.sleep = old_sleep
