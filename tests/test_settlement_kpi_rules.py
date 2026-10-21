import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_functions(*names):
    tree = ast.parse((ROOT / 'dashboard/app.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    scope = {
        'SETTLEMENT_COMPLETE_PHRASES': {'Complete Settlement', 'Settlement Complete'},
        'clean': lambda value: (str(value).strip() if value is not None and str(value).strip() else None),
        'purchase_amount_idr_for_margin': lambda row: float(row.purchase_amount or 0),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<settlement-rules>', 'exec'), scope)
    return [scope[name] for name in names]


class SettlementKpiRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        is_pending, filter_rows, kpis = load_functions(
            '_settlement_check_is_pending', '_settlement_filter_rows', '_settlement_kpis')
        cls.is_pending = staticmethod(is_pending)
        cls.filter_rows = staticmethod(filter_rows)
        cls.kpis = staticmethod(kpis)

    @staticmethod
    def row(**values):
        defaults = dict(
            exception_process='', purchasing_currency='IDR',
            so_status='Delivery Completed', purchasing_settlement_check='Not Settled',
            sales_settlement_status='Not Settled', matched_po_number='PO-1',
            so_number='SO-1', settlement_status='Not Settled',
            purchasing_price=100, purchase_amount=100, sales_amount=150,
        )
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def test_non_idr_delivery_completed_is_settled_for_both_checks(self):
        row = self.row(purchasing_currency='USD')
        self.assertFalse(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertFalse(self.is_pending(row, 'sales_settlement_status'))

    def test_pending_view_excludes_completed_non_idr_row(self):
        row = self.row(purchasing_currency='EUR')
        self.assertEqual(self.filter_rows([row], view='pending'), [])

    def test_blank_currency_uses_local_settlement_status(self):
        row = self.row(purchasing_currency=None)
        self.assertTrue(self.is_pending(row, 'purchasing_settlement_check'))

    def test_non_idr_before_delivery_is_pending(self):
        row = self.row(purchasing_currency='EUR', so_status='In Delivery',
                       purchasing_settlement_check='Complete Settlement',
                       sales_settlement_status='Settlement Complete')
        self.assertTrue(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertTrue(self.is_pending(row, 'sales_settlement_status'))

    def test_exception_process_always_settles_return_item(self):
        row = self.row(purchasing_currency='EUR', so_status='In Delivery',
                       exception_process='Collect')
        self.assertFalse(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertFalse(self.is_pending(row, 'sales_settlement_status'))

    def test_zero_purchasing_price_delivery_completed_is_settled(self):
        row = self.row(purchasing_price=0)
        self.assertFalse(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertFalse(self.is_pending(row, 'sales_settlement_status'))

    def test_blank_purchasing_price_delivery_completed_is_settled(self):
        row = self.row(purchasing_price=None)
        self.assertFalse(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertFalse(self.is_pending(row, 'sales_settlement_status'))

    def test_empty_string_purchasing_price_delivery_completed_is_settled(self):
        row = self.row(purchasing_price='')
        self.assertFalse(self.is_pending(row, 'purchasing_settlement_check'))
        self.assertFalse(self.is_pending(row, 'sales_settlement_status'))

    def test_kpis_count_unique_pending_po_and_so_only(self):
        rows = [
            self.row(),
            self.row(matched_po_number='PO-2', so_number='SO-2', purchasing_currency='USD'),
            self.row(matched_po_number='PO-3', so_number='SO-3', exception_process='Return'),
        ]
        result = self.kpis(rows)
        self.assertEqual(result['purchasing_not_settled_count'], 1)
        self.assertEqual(result['sales_not_settled_count'], 1)
        self.assertEqual(result['purchasing_not_settled_rows'], 1)
        self.assertEqual(result['sales_not_settled_rows'], 1)
        self.assertEqual(result['settled_po_count'], 2)

    def test_floating_scrollbar_track_is_visible(self):
        bundle = (ROOT / 'dashboard/static/assets/index-C9BxB1mr.js').read_text('utf-8')
        index = (ROOT / 'dashboard/static/index.html').read_text('utf-8')
        self.assertIn('background: rgba(148, 163, 184, 0.20);', bundle)
        self.assertIn('box-sizing: border-box;', bundle)
        self.assertIn('z=e.width&&t.clientWidth?e.width/t.clientWidth:1', bundle)
        self.assertIn('"90-180":`#C2410C`', bundle)
        self.assertIn("label:'90-180 Days',color:'#c2410c'", bundle)
        self.assertIn('label:`90-180 Days`,value:e.days_90_180,color:`#C2410C`', bundle)
        self.assertNotIn('#F97316', bundle)
        self.assertNotIn('#f97316', bundle)
        self.assertIn('/assets/index-C9BxB1mr.js?v=desktop-28', index)

    def test_fx_request_identifies_desktop_client(self):
        backend = (ROOT / 'dashboard/app.py').read_text('utf-8')
        self.assertIn("urllib.request.Request(url, headers={'User-Agent': 'ServeoneDashboard/1.0'})", backend)
        self.assertIn('_FX_FETCH_CAP_PER_CALL = 64', backend)
        self.assertIn("runtime_cache_key('settlement_data_v4')", backend)


if __name__ == '__main__':
    unittest.main()
