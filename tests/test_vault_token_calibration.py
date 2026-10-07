import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
MODULE = SCRIPTS / 'vault_token_calibration.py'


class TokenCalibrationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('calibration_test_module', MODULE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def sample(self, hangul, other, tokens, model='fixture-model', generation='fixture-v1'):
        return {'text_sha256': hashlib.sha256(f'{hangul}/{other}'.encode()).hexdigest(),
                'hangul_chars': hangul, 'other_chars': other, 'tokens': tokens,
                'model_id': model, 'generation': generation, 'measurement_origin': 'reported_sample'}

    def samples(self):
        return [self.sample(100, 0, 200), self.sample(0, 100, 25), self.sample(50, 200, 150)]

    def test_fit_model_generation_bound_measurements_without_default_change(self):
        result = self.module.calibrate(self.samples(), 'fixture-model', 'fixture-v1')
        self.assertAlmostEqual(result['coefficients']['hangul'], 2.0)
        self.assertAlmostEqual(result['coefficients']['other'], .25)
        self.assertEqual(result['measurement_origins'], ['reported_sample'])
        self.assertEqual(result['authority'], 'reported_measurements_not_independent_verification')
        self.assertEqual(self.module.estimate('가나다abcd'), 5)
        self.assertEqual(self.module.estimate('가나다abcd', calibration=result,
                         model_id='fixture-model', generation='fixture-v1'), 7)

    def test_wrong_model_generation_stale_or_tampered_selection_rejected(self):
        result = self.module.calibrate(self.samples(), 'fixture-model', 'fixture-v1')
        for model, generation in [('other', 'fixture-v1'), ('fixture-model', 'v2')]:
            with self.subTest(model=model, generation=generation):
                with self.assertRaises(self.module.CalibrationError):
                    self.module.estimate('가a', calibration=result, model_id=model, generation=generation)
        result['coefficients']['hangul'] = 100
        with self.assertRaises(self.module.CalibrationError):
            self.module.estimate('가a', calibration=result, model_id='fixture-model', generation='fixture-v1')

    def test_nonfinite_bool_negative_and_forged_origin_samples_rejected(self):
        for key, value in [('tokens', math.nan), ('tokens', True), ('tokens', -1),
                           ('other_chars', math.inf), ('measurement_origin', 'verified_by_me'),
                           ('model_id', 'another-model'), ('text_sha256', 'x' * 64)]:
            rows = self.samples()
            rows[0][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(self.module.CalibrationError):
                    self.module.calibrate(rows, 'fixture-model', 'fixture-v1')

    def test_rank_deficient_or_duplicate_measurements_cannot_calibrate(self):
        for rows in [[self.sample(0, 100, 25), self.sample(0, 200, 50)],
                     [self.samples()[0], self.samples()[0]], []]:
            with self.assertRaises(self.module.CalibrationError):
                self.module.calibrate(rows, 'fixture-model', 'fixture-v1')

    def test_drift_reports_large_error_without_mutating_coefficients(self):
        result = self.module.calibrate(self.samples(), 'fixture-model', 'fixture-v1')
        self.assertFalse(self.module.check_drift(result, self.samples())['drift'])
        shifted = [dict(row, tokens=row['tokens'] * 2) for row in self.samples()]
        drift = self.module.check_drift(result, shifted, threshold=.2)
        self.assertTrue(drift['drift'])
        self.assertAlmostEqual(drift['mean_relative_error'], .5)
        self.assertEqual(result['coefficients']['hangul'], 2)

    def test_optional_tokenizer_reports_unavailable_instead_of_inventing_counts(self):
        with patch.dict(sys.modules, {'tiktoken': None}):
            with self.assertRaisesRegex(self.module.CalibrationError, 'optional_tokenizer_unavailable'):
                self.module.measure_local('가나다 ABC', 'gpt-4o', 'test-v1')

    def test_external_counter_requires_network_optin_and_rejects_sensitive_input(self):
        called = []
        def counter(**request):
            called.append(request)
            return 7
        with self.assertRaises(self.module.CalibrationError):
            self.module.measure_counter('hello', 'fixture-model', 'test-v1', counter=counter)
        with self.assertRaises(self.module.CalibrationError):
            self.module.measure_counter('ghp_' + 'x' * 36, 'fixture-model', 'test-v1',
                                        counter=counter, allow_network=True)
        self.assertEqual(called, [])
        result = self.module.measure_counter('hello', 'fixture-model', 'test-v1',
                                             counter=counter, allow_network=True)
        self.assertEqual(result['tokens'], 7)
        self.assertEqual(result['measurement_origin'], 'host_counter')
        self.assertNotIn('text', result)

    def test_invalid_external_result_never_becomes_measured_sample(self):
        for result in [True, -1, float('nan'), {'input_tokens': 7}]:
            with self.subTest(result=result):
                with self.assertRaises(self.module.CalibrationError):
                    self.module.measure_counter('hello', 'fixture-model', 'test-v1',
                        counter=lambda **request: result, allow_network=True)

    def test_hangul_filler_secret_is_not_sent_to_host_counter(self):
        called = []
        token = ('ghp_' + 'x' * 36).replace('ghp_', 'ghp_\u3164')
        with self.assertRaisesRegex(self.module.CalibrationError, 'sensitive_input'):
            self.module.measure_counter(token, 'fixture-model', 'test-v1',
                counter=lambda **request: called.append(request), allow_network=True)
        self.assertEqual(called, [])

    def test_korean_identifier_inspection_blocks_host_counter_before_transport(self):
        called = []
        identifier = '900101-1234567'
        cases = [identifier, '９００１０１－１２３４５６７']
        cases.extend(identifier[:3] + splitter + identifier[3:] for splitter in
                     ('\u115f', '\u1160', '\u3164', '\uffa0', '\u200b', '\ufe0f', '\U000e0100'))
        for text in cases:
            with self.subTest(text_kind='synthetic_identifier'):
                with self.assertRaisesRegex(self.module.CalibrationError, 'sensitive_input'):
                    self.module.measure_counter(text, 'fixture-model', 'fixture-generation',
                        counter=lambda **request: called.append(request) or 7, allow_network=True)
        self.assertEqual(called, [])

    def test_harmless_dates_phones_and_business_numbers_keep_original_counter_input(self):
        for text in ('2026-10-07', '010-1234-5678', '123-45-67890', '900132-1234567'):
            called = []
            result = self.module.measure_counter(text, 'fixture-model', 'fixture-generation',
                counter=lambda **request: called.append(request) or 7, allow_network=True)
            self.assertEqual(called, [{'model': 'fixture-model', 'text': text}])
            self.assertEqual(result['measurement_origin'], 'host_counter')
            self.assertEqual(result['text_sha256'], hashlib.sha256(text.encode()).hexdigest())

    def test_incomplete_egress_inspection_never_invokes_host_counter(self):
        import vault_egress
        called = []
        with patch.object(vault_egress, 'MAX_NOTE_BYTES', 4):
            with self.assertRaisesRegex(self.module.CalibrationError, 'inspection_incomplete'):
                self.module.measure_counter('hello', 'fixture-model', 'fixture-generation',
                    counter=lambda **request: called.append(request) or 7, allow_network=True)
        self.assertEqual(called, [])

    def test_multiline_credentials_keep_whole_input_guard_before_transport(self):
        called = []
        labels = [('password', 'abcdefgh'), ('api_key', 'abcdefgh'),
                  ('authorization', 'Bearer abcdefgh')]
        for label, value in labels:
            for separator in ('\n', '\r\n', '\u2028'):
                for splitter in ('', '\u3164', '\u115f', '\u200b', '\ufe0f'):
                    text = label[:2] + splitter + label[2:] + ':' + separator + value
                    with self.subTest(label=label, separator=repr(separator), splitter=repr(splitter)):
                        with self.assertRaisesRegex(self.module.CalibrationError, 'sensitive_input'):
                            self.module.measure_counter(text, 'fixture-model', 'fixture-generation',
                                counter=lambda **request: called.append(request) or 7, allow_network=True)
        self.assertEqual(called, [])

    def test_harmless_multiline_text_is_measured_without_rewriting_original(self):
        text = 'Password policy discussion.\nAPI key rotation guidance.\r\nAuthorization overview.'
        called = []
        result = self.module.measure_counter(text, 'fixture-model', 'fixture-generation',
            counter=lambda **request: called.append(request) or 7, allow_network=True)
        self.assertEqual(called, [{'model': 'fixture-model', 'text': text}])
        self.assertEqual(result['text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(result['measurement_origin'], 'host_counter')


if __name__ == '__main__':
    unittest.main()
