import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReportDownloadTests(unittest.TestCase):
    def test_report_assets_and_date_range_contract(self):
        index = (ROOT / 'dashboard/static/index.html').read_text(encoding='utf-8')
        script = (ROOT / 'dashboard/static/desktop/reports.js').read_text(encoding='utf-8')
        backend = (ROOT / 'dashboard/app.py').read_text(encoding='utf-8')

        self.assertIn('/desktop/reports.js?v=8', index)
        self.assertIn('/desktop/reports.css?v=9', index)
        self.assertIn('Compact sidebar: reuse menu labels as fast hover/focus tooltips.', index)
        self.assertIn('.nav-side:hover .nav-label,.nav-side:focus-within .nav-label{opacity:0}', index)
        self.assertIn('Last 7 Days', script)
        self.assertIn('Last 30 Days', script)
        self.assertIn('Last 12 Months', script)
        self.assertIn("date_from:range.from,date_to:range.to", script)
        self.assertIn('Print / Save as PDF', script)
        self.assertIn('Printable report preview', script)
        self.assertIn('Cost Reduction & PIC KPI', script)
        self.assertIn("button.nextElementSibling!==autoUpdate", script)
        self.assertIn('aria-label\',\'Create report', script)
        self.assertIn('function visualBars', script)
        self.assertIn('function segments', script)
        self.assertIn('function coverPage', script)
        self.assertIn('Reporting Period', script)
        self.assertIn('--primary:#2563eb', script)
        self.assertIn('--good:#047857', script)
        self.assertIn('--good-fill:#14b8a6', script)
        self.assertNotIn('SERVE<span>ONE</span>', script)
        self.assertIn("event.key==='Escape'", script)
        self.assertNotIn('<h2>Raw Data</h2>', script)
        self.assertIn('def _cr_build_base_v632722(settings, selected_from=None, selected_to=None):', backend)
        self.assertIn("'comparison_from': previous_start.isoformat()", backend)


if __name__ == '__main__':
    unittest.main()
