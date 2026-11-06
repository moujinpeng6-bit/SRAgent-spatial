import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sragent_spatial.multiagent import SpatialSession


class MultiAgentTests(unittest.TestCase):
    def session(self, root):
        out = root / 'out'
        out.mkdir()
        return SpatialSession('Homo sapiens', 'lung', ['Visium'], out, root / 'assets', 100, 3, 2)

    def test_model_cannot_skip_discovery_or_choose_unseen_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(Path(folder))
            with self.assertRaises(ValueError):
                session.read_study('S-BSST1410', 'Visium')
            with self.assertRaises(ValueError):
                session.search_catalog('Xenium')
            with self.assertRaises(ValueError):
                session.prepare_files('S-BSST1410', 'Visium')
            with self.assertRaises(ValueError):
                session.validate_sample('unseen', 'Visium')
            self.assertFalse(session.report()['workflow_complete'])

    def test_changed_file_goes_back_to_preparation_and_requires_actual_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(Path(folder))
            accession, sample = 'S-BSST1410', 'S-BSST1410--abc'
            session.hits = {'Visium': [accession]}
            row = {'source': 'BioStudies', 'study_id': accession, 'sample_id': sample, 'platform': 'Visium',
                   'found': True, 'selected': True, 'files_ready': True, 'status': 'files_prepared', 'files': {}}
            target = session.assets / accession / 'Visium/abc'
            target.mkdir(parents=True)
            for role in ('h5', 'image', 'positions', 'scalefactors'):
                path = target / role
                path.write_bytes(role.encode())
                row['files'][role] = {'member': 'source/' + role, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            session.studies[accession, 'Visium'] = {'row': row, 'selected': True, 'prepared': True, 'inspected': True}
            session.samples['Visium:' + sample] = row
            (target / 'positions').write_bytes(b'wrong')
            with patch('sragent_spatial.multiagent.validate_prepared_files') as validate:
                self.assertEqual(session.validate_sample(sample, 'Visium')['status'], 'missing_or_changed_files')
                validate.assert_not_called()
            self.assertFalse(session.report()['workflow_complete'])
            (target / 'positions').write_bytes(b'positions')
            row['status'] = 'files_prepared'
            with patch('sragent_spatial.multiagent.validate_prepared_files', return_value={
                'status': 'correspondence_passed', 'checks': {'actual': True}, 'files_ready': True
            }) as validate:
                self.assertEqual(session.validate_sample(sample, 'Visium')['status'], 'correspondence_passed')
                validate.assert_called_once()
            self.assertTrue(session.report()['workflow_complete'])

    def test_retry_bound_and_error_trace_preserve_failure(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(Path(folder))
            with patch.object(session, 'search_catalog', side_effect=TimeoutError('controlled')) as search:
                results = [json.loads(session.execute('search', 'search_catalog', platform='Visium')) for _ in range(4)]
            self.assertEqual(search.call_count, 3)
            self.assertTrue(results[0]['retryable'])
            self.assertFalse(results[2]['retryable'])
            self.assertFalse(results[3]['retryable'])
            self.assertEqual(len(session.events), 4)
