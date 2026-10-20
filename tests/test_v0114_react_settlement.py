"""Release regressions for React-owned Pending Settlement in Summary.

Uses source inspection only (the full built dashboard still needs WebView2 QA).
No live company DB or external network access.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'dashboard/static/assets/index-C9BxB1mr.js'
NATIVE = ROOT / 'dashboard/static/desktop/pending_settlement_react.source.js'
CSS = ROOT / 'dashboard/static/desktop/pending_settlement_react.css'
INDEX = ROOT / 'dashboard/static/index.html'


class ReactSettlementTests(unittest.TestCase):
    def test_legacy_dom_widget_removed_in_favor_of_react_component(self):
        js = JS.read_text('utf-8')
        self.assertIn('function PSReactSummarySection(', js)
        self.assertIn('const h = y.createElement;', js)
        self.assertEqual(js.count('PSReactSummarySection,{url:Za('), 1)
        self.assertIn('onPending:ko,darkMode:n', js)
        self.assertIn(' Pending Delivery Aging', js)
        for legacy in ('window.__v63PSW', 'function onSummaryPage()',
                       'function findAnchorContainer()',
                       'anchor.parentNode.insertBefore(section, anchor)',
                       'WATCH_MS = 3000'):
            self.assertNotIn(legacy, js)

    def test_template_and_background_data_are_separate_react_states(self):
        native = NATIVE.read_text('utf-8')
        for code in (
            'setSnapshot({url,data:cached,error:null,loading:!cached})',
            "if(!alive||urlRef.current!==url)return;",
            "if(!alive||urlRef.current!==url||error.name==='AbortError')return;",
            'controller.abort()',
            "window.addEventListener('serveone:data-changed',live)",
            "window.removeEventListener('serveone:data-changed',live)",
            "document.addEventListener('visibilitychange',resume)",
            "if(!value||typeof value!=='object'||!value.kpi||!value.heatmap)",
            'const kpi=data?.kpi||{};',
            "h('div',{className:'ps-react-grid'}",
            "h('table',{className:'ps-react-table'}",
            "h('section',{className:'ps-react-summary'",
        ):
            with self.subTest(code=code):
                self.assertIn(code, native)
        self.assertNotIn('MutationObserver(', native)
        self.assertNotIn('document.querySelectorAll', native)
        self.assertNotIn('window.location.reload', native)

    def test_theme_sticky_table_modal_and_styles_are_loaded(self):
        style = CSS.read_text('utf-8')
        index = INDEX.read_text('utf-8')
        self.assertIn('/desktop/pending_settlement_react.css?v=14', index)
        self.assertIn('/assets/index-C9BxB1mr.js?v=desktop-24', index)
        for cls in ('.ps-react-dark', '.ps-react-table-scroll', '.ps-react-sticky-left',
                    '.ps-react-modal', '.ps-react-kpis', '.ps-react-skeleton-line',
                    '.ps-react-grand-row'):
            self.assertIn(cls, style)
        self.assertIn('data\\/pending-settlement(?:-detail)?', index)
        self.assertNotIn('company_password', style)

    def test_backend_api_contract_unchanged(self):
        backend = (ROOT / 'dashboard/app.py').read_text('utf-8')
        self.assertIn("@app.route('/api/data/pending-settlement', methods=['GET'])", backend)
        self.assertIn("@app.route('/api/data/pending-settlement-detail', methods=['GET'])", backend)
        self.assertIn("'heatmap': heatmap, 'pie': pie, 'combo': combo_list", backend)
        self.assertIn("'monthly': monthly", backend)

    def test_no_data_or_installer_requirement_changed(self):
        installer = (ROOT/'installer.iss').read_text('utf-8')
        build = (ROOT/'build_windows.bat').read_text('utf-8')
        self.assertIn('0.1.14-preview', installer)
        self.assertIn('ServeoneDashboard-Desktop-Setup-V0.1.14', installer)
        self.assertIn('desktop_launcher.py', build)
        self.assertIn("@('instance','__pycache__')", build)
        self.assertIn("@('.db','.sqlite','.sqlite3','.dpapi')", build)
        self.assertIn('--add-data "%BUILD_DASHBOARD%;dashboard"', build)


if __name__ == '__main__':
    unittest.main()
