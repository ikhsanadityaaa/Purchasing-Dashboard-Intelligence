"""Regressions for local data-revision refresh with synthetic inputs only."""
import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def endpoint(name, scope):
    tree=ast.parse((ROOT/'desktop_runtime.py').read_text(encoding='utf-8'))
    outer=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register_desktop_routes')
    fn=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name==name)
    fn.decorator_list=[]
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<local-endpoint>','exec'),scope)
    return scope[name]

class DummyResponse:
    def __init__(self,payload):
        self.payload=payload
        self.headers={}

class LiveRefreshTests(unittest.TestCase):
    def test_revision_marker_endpoint_has_no_company_data(self):
        with tempfile.TemporaryDirectory() as path:
            runtime=SimpleNamespace(data_dir=Path(path))
            scope={'runtime':runtime,'jsonify':lambda d:DummyResponse(d)}
            handler=endpoint('desktop_data_revision',scope)
            res=handler()
            self.assertEqual(res.payload,{'revision':''})
            self.assertEqual(res.headers['Cache-Control'],'no-store, max-age=0')
            marker=Path(path)/'instance'/'data_revision.txt'
            marker.parent.mkdir(parents=True)
            marker.write_text('synthetic-revision-123\n',encoding='ascii')
            res=handler()
            self.assertEqual(res.payload,{'revision':'synthetic-revision-123'})
            self.assertEqual(set(res.payload),{'revision'})

    def test_only_successful_committed_writes_publish(self):
        import sys
        class Backend:
            _UPLOAD_JOB_EXECUTING=SimpleNamespace(on=False)
            def __init__(self):self.calls=[]
            def _soft_invalidate_response_caches(self):self.calls.append('invalidate')
            def _publish_data_revision(self):self.calls.append('publish')
        backend=Backend()
        request=SimpleNamespace(method='POST',path='/api/upload/smro')
        logger=SimpleNamespace(warning=lambda *args:None)
        with patch.dict(sys.modules,{'app':backend}):
            callback=endpoint('desktop_publish_inline_changes',{'request':request,'app':SimpleNamespace(logger=logger)})
            for status in (202,400,401,500):
                callback(SimpleNamespace(status_code=status))
            self.assertEqual(backend.calls,[])
            callback(SimpleNamespace(status_code=200))
            self.assertEqual(backend.calls,['invalidate','publish'])
            backend.calls.clear()
            request.method='GET'
            callback(SimpleNamespace(status_code=200))
            request.method='POST';request.path='/api/desktop/run'
            callback(SimpleNamespace(status_code=200))
            self.assertEqual(backend.calls,[])
            request.path='/api/item-registration/456';request.method='PUT'
            callback(SimpleNamespace(status_code=200))
            self.assertEqual(backend.calls,['invalidate','publish'])
            backend.calls.clear()
            backend._UPLOAD_JOB_EXECUTING.on=True
            callback(SimpleNamespace(status_code=200))
            self.assertEqual(backend.calls,[])

    def test_live_monitor_is_local_and_no_full_reload(self):
        js=(ROOT/'dashboard/static/desktop/live_data.js').read_text('utf-8')
        self.assertIn("'/api/desktop/data-revision'",js)
        self.assertIn('serveone:data-changed',js)
        self.assertIn('setInterval(check,2500)',js)
        self.assertNotIn('location.reload',js)
        self.assertNotIn('http://',js)
        self.assertNotIn('https://',js)
        self.assertIn('/desktop/live_data.js', (ROOT/'dashboard/static/index.html').read_text('utf-8'))

    def test_all_key_pages_revalidate_without_navigating(self):
        js=(ROOT/'dashboard/static/assets/index-C9BxB1mr.js').read_text('utf-8')
        self.assertIn('window.addEventListener(`serveone:data-changed`,handle)',js)
        self.assertIn("window.addEventListener('serveone:data-changed',onLive)",js)
        self.assertIn("window.addEventListener('serveone:data-changed',live);",js)
        self.assertNotIn('window.__v63PSW',js)
        self.assertIn('function PSReactSummarySection(',js)
        self.assertIn('XG();tK();',js)
        self.assertIn('XG();tK();\n    Ya(Ma);U();',js)
        self.assertIn('else if(i===`settlement`){SHS(e=>e+1)}',js)
        self.assertIn('else if(i===`item-registration`)$a(Je,Xe,nt,N,yt,Ma)',js)
        self.assertIn('else if(i===`vendor-control`)ao(_i,yi,Ci,!1,Di)',js)
        backend=(ROOT/'dashboard/app.py').read_text('utf-8')
        self.assertIn('func.max(SOData.updated_at)',backend)
        self.assertIn('func.max(ItemRegistration.updated_at)',backend)

    def test_dashboard_uses_restart_snapshot_and_long_browser_cache(self):
        backend=(ROOT/'dashboard/app.py').read_text('utf-8')
        bundle=(ROOT/'dashboard/static/assets/index-C9BxB1mr.js').read_text('utf-8')
        self.assertIn("_DASHBOARD_SNAPSHOT_DIR",backend)
        self.assertIn("payload = _dashboard_snapshot_load(key)",backend)
        self.assertIn("_swr_spawn_refresh(key, refresh_url)",backend)
        self.assertIn("_DASHBOARD_SNAPSHOT_FRESH_SECONDS = 10 * 60",backend)
        self.assertIn("'completed_summary',",backend)
        self.assertIn('xG=604800*1e3',bundle)

    def test_header_button_normal_flow_and_matching_height(self):
        src=(ROOT/'dashboard/static/desktop/auto_update.js').read_text('utf-8')
        css=(ROOT/'dashboard/static/desktop/auto_update.css').read_text('utf-8')
        self.assertIn('parent.insertBefore(button,wrap)',src)
        self.assertIn("const measured=manual.offsetHeight",src)
        self.assertNotIn("Math.round(m.left-p.left-133)",src)
        self.assertIn('position:static!important',css)
        self.assertIn('stroke-linejoin="round"',src)

if __name__=='__main__':unittest.main()
