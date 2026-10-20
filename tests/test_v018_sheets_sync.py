"""Offline-first Google Sheets sync tests with synthetic records, no internet/Windows."""
from __future__ import annotations
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desktop_sheet_sync import VendorSnapshotStore, SheetSyncCoordinator
from desktop_google_readonly import GoogleReadOnlyAccess, parse_a1, slice_a1_rows, SCOPES
from desktop_network_guard import (_allowed_hostname,_authorize,_scope,
                                   permit_google_sheets_network,google_sync_is_allowed)

SYNTHETIC_KEY={'type':'service_account','client_email':'tests@example.iam.gserviceaccount.com',
               'private_key':'-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----\n',
               'token_uri':'https://oauth2.googleapis.com/token'}

def fake_protect(data,decrypt=False,purpose=b''):
    """Test-only reversible transformation; production uses Windows DPAPI."""
    if decrypt:return data[2:][::-1]
    return b'T:' + data[::-1]

class FakeApp:
    def app_context(self):return contextlib.nullcontext()

class FakeBackend:
    def __init__(self):
        self.import_runs=0
        self.vendor_runs=0
        self.revisions=0
        self.invalidations=0
        self.import_result={'added':3,'updated':1,'seen':7,'sheet_rows':11,'source_rows_added':2}
        self.fail_vendor=False
        self.release=threading.Event();self.release.set()
        self.db=SimpleNamespace(session=SimpleNamespace(rollback=lambda:None))
    def sync_import_sheet_to_dashboard(self):
        self.import_runs+=1
        self.release.wait(1)
        return dict(self.import_result)
    def vendor_control_rows(self,force=False):
        self.vendor_runs+=1
        if self.fail_vendor:raise RuntimeError('Google unreachable (synthetic)')
        return [{'row_key':'2','vendor_name':'Example','vendor_id':'abc',
                 'password':'synthetic-secret'}],None
    def clear_runtime_caches(self):self.invalidations+=1
    def _publish_data_revision(self):self.revisions+=1

class DummyCredentials:
    def __init__(self,configured=True):self.configured=configured
    def status(self):return {'configured':self.configured}

class DummySnapshot:
    def __init__(self):
        self.path=Path('/test/not-real')
        self.rows=[]
        self.writes=0
    def save(self,rows,fetched_at):
        self.rows=rows;self.writes+=1

class SheetSyncTests(unittest.TestCase):
    def wait_until(self,predicate,seconds=3):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.008)
        self.fail('Background worker did not settle')

    def test_first_visit_once_even_after_navigating_and_manual_retry(self):
        backend=FakeBackend();snap=DummySnapshot()
        svc=SheetSyncCoordinator(DummyCredentials(),snap,backend,FakeApp())
        self.assertTrue(svc.start('import','once')['accepted'])
        self.wait_until(lambda:svc.status()['import']['state']=='completed')
        self.assertFalse(svc.start('import','once')['accepted'])
        self.assertFalse(svc.start('import','once')['accepted'])
        self.assertEqual(backend.import_runs,1)
        self.assertEqual(backend.revisions,1)
        self.assertTrue(svc.start('import','manual')['accepted'])
        self.wait_until(lambda:svc.status()['import']['state']=='completed' and backend.import_runs==2)
        self.assertEqual(backend.revisions,2)
        self.assertEqual(svc.status()['import']['stats']['added'],3)

    def test_distinct_pages_once_and_no_concurrent_duplicate(self):
        backend=FakeBackend();backend.release.clear()
        svc=SheetSyncCoordinator(DummyCredentials(),DummySnapshot(),backend,FakeApp())
        self.assertTrue(svc.start('import','once')['accepted'])
        self.assertEqual(svc.start('import','manual')['reason'],'already_running')
        self.assertTrue(svc.start('vendor','once')['accepted'])
        self.wait_until(lambda:svc.status()['vendor']['state']=='completed')
        self.assertEqual(backend.vendor_runs,1)
        self.assertEqual(svc.start('vendor','once')['reason'],'already_attempted')
        backend.release.set()
        self.wait_until(lambda:svc.status()['import']['state']=='completed')
        self.assertEqual(backend.import_runs,1)

    def test_vendor_failure_preserves_offline_snapshot_and_can_retry(self):
        backend=FakeBackend();backend.fail_vendor=True
        snapshot=DummySnapshot();snapshot.rows=[{'vendor_name':'Old offline vendor'}]
        svc=SheetSyncCoordinator(DummyCredentials(),snapshot,backend,FakeApp())
        svc.start('vendor','once')
        self.wait_until(lambda:svc.status()['vendor']['state']=='failed')
        self.assertEqual(snapshot.writes,0)
        self.assertEqual(snapshot.rows[0]['vendor_name'],'Old offline vendor')
        self.assertEqual(backend.revisions,0)
        backend.fail_vendor=False
        svc.start('vendor','manual')
        self.wait_until(lambda:svc.status()['vendor']['state']=='completed')
        self.assertEqual(snapshot.writes,1)
        self.assertEqual(backend.revisions,1)

    def test_unauthenticated_public_sync_still_attempted_once(self):
        backend=FakeBackend();snap=DummySnapshot();creds=DummyCredentials(True)
        svc=SheetSyncCoordinator(creds,snap,backend,FakeApp())
        self.assertTrue(svc.start('import','once')['accepted'])
        self.wait_until(lambda:svc.status()['import']['state']=='completed')
        self.assertFalse(svc.start('import','once')['accepted'])
        self.assertEqual(backend.import_runs,1)
        self.assertEqual(backend.revisions,1)

    def test_oauth_readonly_client_encrypted_and_service_account_rejected(self):
        client={'installed':{'client_id':'example.apps.googleusercontent.com',
            'client_secret':'public-oauth-client','auth_uri':'https://accounts.google.com/o/oauth2/auth',
            'token_uri':'https://oauth2.googleapis.com/token'}}
        with tempfile.TemporaryDirectory() as tmp,patch('desktop_google_readonly._crypt',fake_protect):
            store=GoogleReadOnlyAccess(tmp)
            self.assertTrue(store.status()['configured'])  # public CSV needs no secret
            self.assertFalse(store.status()['oauth_connected'])
            self.assertTrue(store.save(client)['oauth_client_configured'])
            self.assertNotIn(b'public-oauth-client',store.client_path.read_bytes())
            self.assertIn('spreadsheets.readonly',SCOPES[0])
            with self.assertRaises(ValueError):store.save(SYNTHETIC_KEY)
            evil={'installed':dict(client['installed'],token_uri='https://evil.example')}
            with self.assertRaises(ValueError):store.save(evil)
    def test_vendor_snapshot_encrypted_and_edits_persist_offline(self):
        with tempfile.TemporaryDirectory() as tmp,patch('desktop_sheet_sync._crypt',fake_protect):
            db=VendorSnapshotStore(Path(tmp)/'vendor.dpapi')
            rows=[{'row_key':'4','vendor_name':'Example','vendor_id':'user','password':'SENSITIVE-TEST'}]
            db.save(rows)
            self.assertNotIn(b'SENSITIVE-TEST',db.path.read_bytes())
            self.assertEqual(db.load()[0][0]['password'],'SENSITIVE-TEST')
            self.assertTrue(db.edit('4','password','NEW-TEST'))
            self.assertEqual(db.load()[0][0]['password'],'NEW-TEST')
            self.assertFalse(db.edit('99','password','other'))
            with self.assertRaises(ValueError):db.edit('4','vendor_name','evil')

    def test_google_outbound_requires_trusted_thread_scope_and_resolved_peer(self):
        self.assertTrue(_allowed_hostname('docs.google.com'))
        self.assertTrue(_allowed_hostname('oauth2.googleapis.com'))
        self.assertTrue(_allowed_hostname('doc-13.googleusercontent.com'))
        self.assertFalse(_allowed_hostname('evil-google.com'))
        self.assertFalse(_allowed_hostname('example.org'))
        self.assertFalse(google_sync_is_allowed())
        with self.assertRaises(PermissionError):_authorize(('203.0.113.8',443))
        with permit_google_sheets_network():
            self.assertTrue(google_sync_is_allowed())
            _scope.ips.add(('203.0.113.8',443))
            self.assertTrue(_authorize(('203.0.113.8',443)))
            with self.assertRaises(PermissionError):_authorize(('203.0.113.9',443))
            with self.assertRaises(PermissionError):_authorize(('203.0.113.8',80))
        self.assertFalse(google_sync_is_allowed())
        with self.assertRaises(PermissionError):_authorize(('203.0.113.8',443))

    def test_frontend_does_not_sync_on_every_navigation(self):
        source=(ROOT/'dashboard/static/desktop/sheet_sync.js').read_text('utf8')
        html=(ROOT/'dashboard/static/index.html').read_text('utf8')
        self.assertIn("const attempted=new Set()",source)
        self.assertIn("if(mode==='once'&&attempted.has(page))return",source)
        self.assertIn("if(mode==='once')attempted.add(page)",source)
        self.assertIn("start('once')",source)
        self.assertIn("start('manual')",source)
        self.assertNotIn('location.reload(',source)
        self.assertIn('/desktop/sheet_sync.js?v=19',html)
        self.assertIn('Syncing in background; offline data remains visible',source)
        self.assertIn('oauth_client_configured',source)
        self.assertIn('Google Sheets · Read-only Login',source)
        self.assertNotIn("menuButton('Sync Google Sheets'",source)

    def test_import_sync_uses_existing_upsert_and_failure_guard(self):
        source=(ROOT/'dashboard/app.py').read_text('utf8')
        self.assertIn('def sync_import_sheet_to_dashboard():',source)
        self.assertIn("safe_to_purge = not source_errors and bool(sheet_rows)",source)
        self.assertIn("and safe_to_purge:",source)
        self.assertIn("get('SERVEONE_DESKTOP_MODE') == '1' and not safe_to_purge",source)
        self.assertIn("get('SERVEONE_DESKTOP_MODE') != '1' or safe_to_purge",source)
        self.assertIn("if os.environ.get('SERVEONE_DESKTOP_MODE') == '1': raise",source)
        self.assertIn('no vendor filter: using',source)
        self.assertNotIn('layout sheet rows skipped',source)

if __name__=='__main__':unittest.main()
