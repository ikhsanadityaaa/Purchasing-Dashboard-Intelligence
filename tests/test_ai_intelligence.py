import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_sqlalchemy import SQLAlchemy


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'dashboard'
if str(DASHBOARD) not in os.sys.path:
    os.sys.path.insert(0, str(DASHBOARD))

import ai_intelligence as ai


class AIIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ['SERVEONE_DATA_DIR'] = cls.temp.name
        os.environ.pop('HERMES_BASE_URL', None)
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(cls.temp.name) / 'test.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        cls.db = SQLAlchemy(cls.app)
        ai.init_ai_intelligence(cls.app, cls.db, admin_check=lambda: True)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            cls.db.session.remove()
            cls.db.engine.dispose()
        cls.temp.cleanup()

    def test_pricing_formula_is_deterministic(self):
        result = ai.calculate_pricing({
            'purchase_cost': 80, 'sales_price': 100, 'quantity': 10,
            'target_gross_margin': 0.25, 'market_purchase_benchmark': 75,
        })
        self.assertAlmostEqual(result['metrics']['gross_margin'], 0.2)
        self.assertAlmostEqual(result['metrics']['markup'], 0.25)
        self.assertAlmostEqual(result['metrics']['target_selling_price'], 106.6666667)
        self.assertAlmostEqual(result['metrics']['purchase_gap_vs_market'], 5)
        self.assertIn('Quantity remains unchanged', result['assumptions'][0])

    def test_provider_key_is_never_returned(self):
        with patch.object(ai, '_save_secret', return_value='encrypted.bin'):
            response = self.client.post('/api/ai/providers', json={
                'name': 'Test OpenRouter', 'provider': 'openrouter',
                'model_id': 'free-model', 'api_key': 'secret-value',
            })
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertTrue(body['has_api_key'])
        self.assertNotIn('api_key', body)
        self.assertNotIn('secret_ref', body)
        self.assertNotIn('secret-value', response.get_data(as_text=True))

    def test_pricing_task_persists_and_deduplicates(self):
        payload = {
            'reference': 'UNIT-TEST-PRICE', 'purchase_cost': 80,
            'sales_price': 100, 'quantity': 5, 'target_gross_margin': 0.25,
        }
        first = self.client.post('/api/ai/pricing-analysis', json=payload)
        self.assertEqual(first.status_code, 202)
        task_id = first.get_json()['id']
        body = None
        for _ in range(50):
            body = self.client.get('/api/ai/tasks/' + task_id).get_json()
            if body['status'] not in {'QUEUED', 'RUNNING', 'RETRY_WAIT'}:
                break
            time.sleep(0.02)
        self.assertEqual(body['status'], 'COMPLETED')
        self.assertEqual(body['result']['classification'], 'CALCULATED')
        duplicate = self.client.post('/api/ai/pricing-analysis', json=payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()['duplicate'])
        self.assertEqual(duplicate.get_json()['id'], task_id)

    def test_health_reports_missing_hermes_without_breaking(self):
        with patch.object(ai, '_hermes_executable', return_value=None):
            response = self.client.get('/api/ai/health')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['status'], 'AVAILABLE')
        self.assertEqual(body['hermes']['status'], 'UNCONFIGURED')

    def test_hermes_install_requires_explicit_confirmation(self):
        response = self.client.post('/api/ai/hermes/install', json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn('INSTALL_HERMES', response.get_json()['error'])

    def test_hermes_install_starts_only_after_confirmation(self):
        expected = {'installed': False, 'action': {'status': 'INSTALLING'}}
        with patch.object(ai, '_start_hermes_action', return_value=expected) as start:
            response = self.client.post('/api/ai/hermes/install', json={'confirm': 'INSTALL_HERMES'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), expected)
        start.assert_called_once_with('INSTALL')

    def test_hermes_wrapper_provides_sha256_fallback(self):
        wrapper = ai._write_hermes_wrapper('INSTALL')
        source = wrapper.read_text(encoding='utf-8')
        self.assertIn('function Get-FileHash', source)
        self.assertIn('[System.Security.Cryptography.HashAlgorithm]::Create($Algorithm)', source)

    def test_installer_status_does_not_expose_secrets(self):
        with patch.object(ai, '_hermes_executable', return_value=None):
            response = self.client.get('/api/ai/hermes/installer')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body['installed'])
        self.assertNotIn('api_key', response.get_data(as_text=True).lower())

    def test_ui_assets_and_route_contract(self):
        index = (ROOT / 'dashboard/static/index.html').read_text(encoding='utf-8')
        script = (ROOT / 'dashboard/static/desktop/ai_intelligence.js').read_text(encoding='utf-8')
        style = (ROOT / 'dashboard/static/desktop/ai_intelligence.css').read_text(encoding='utf-8')
        self.assertIn('/desktop/ai_intelligence.js?v=5', index)
        self.assertIn('/desktop/ai_intelligence.css?v=9', index)
        self.assertIn("PATH='/AI_Intelligence'", script)
        self.assertIn('Overview', script)
        self.assertIn('Research Tasks', script)
        self.assertIn('Pricing &amp; Margin', script)
        self.assertIn('Settings', script)
        self.assertIn("setAttribute('aria-label','AI Intelligence')", script)
        self.assertIn("setAttribute('aria-current','page')", script)
        self.assertIn('M9 2v4M15 2v4', script)
        self.assertIn('Install Hermes', script)
        self.assertIn('/api/ai/hermes/installer', script)
        self.assertIn('INSTALL_HERMES', script)
        self.assertIn('z-ai-provider-flags', script)
        self.assertIn('@media (max-width:700px)', style)
        self.assertIn('@media (prefers-reduced-motion:reduce)', style)
        self.assertIn('body.z-app-dark #z-ai-intelligence', style)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', style)
        self.assertIn('.z-ai-provider-flags{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}', style)
        self.assertIn('.z-ai-input,.z-ai-select{width:100%;height:40px;min-height:40px', style)
        self.assertIn('.z-ai-shell{min-height:100%;padding:24px 30px 48px calc(var(--nav-collapsed,3.5rem) + 24px)}', style)
        self.assertIn('.nav-side [data-ai-nav]>svg{width:1.25rem;height:1.25rem;flex:0 0 1.25rem}', style)
        self.assertIn("location.pathname.toLowerCase() !== '/ai_intelligence'", index)


if __name__ == '__main__':
    unittest.main()

