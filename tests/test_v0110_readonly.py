"""No Google mutations, read-only sync, public/private access tests."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desktop_google_readonly import GoogleReadOnlyAccess,parse_a1,slice_a1_rows,SCOPES

class ReadOnlyReleaseTests(unittest.TestCase):
    def test_a1_range_slicing_preserves_rows_and_columns(self):
        self.assertEqual(parse_a1("'Vendor Control'!A1:Z"),('Vendor Control','A1','Z'))
        self.assertEqual(parse_a1("'Bob''s List'!B2:D3"),("Bob's List",'B2','D3'))
        rows=[['Name','Account','Password'],['Vendor A','hello','dummy'],['Vendor B','world','dummy2']]
        self.assertEqual(slice_a1_rows(rows,'B2','C3'),[['hello','dummy'],['world','dummy2']])
        self.assertEqual(slice_a1_rows(rows,'A1','Z2'),rows[:2])

    def test_public_csv_read_needs_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            access=GoogleReadOnlyAccess(tmp)
            with patch.object(access,'_request',return_value=b'Name,Value\nA,1\nB,2\n') as request:
                self.assertEqual(access.csv_rows('fake-id','7',{'7':'Demo'}),
                                 [['Name','Value'],['A','1'],['B','2']])
                self.assertIn('/export',request.call_args.args[0])
                self.assertEqual(request.call_args.args[1]['gid'],'7')
            with patch.object(access,'_request',return_value=b'A,B,C\n1,2,3\n'):
                result=access.values('fake-id',"'Demo'!B2:C2",{'7':'Demo'})
                self.assertEqual(result['values'],[['2','3']])

    def test_private_google_sheet_uses_read_only_bearer_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            access=GoogleReadOnlyAccess(tmp)
            with patch.object(access,'access_token',return_value='synthetic-read-only-token'), patch.object(access,'_request',side_effect=[{'sheets':[{'properties':{'sheetId':7,'title':'Private'}}]}, {'values':[['a'],['b']]}]) as call:
                self.assertEqual(access.metadata('fake-id',{'7':'Private'})['sheets'][0]['properties']['title'],'Private')
                data=access.values('fake-id',"'Private'!A1:A2",{'7':'Private'})
                self.assertEqual(data['values'],[['a'],['b']])
                self.assertIn('sheets.googleapis.com',call.call_args.args[0])
                self.assertEqual(call.call_args.args[2],'synthetic-read-only-token')
            self.assertEqual(SCOPES,['https://www.googleapis.com/auth/spreadsheets.readonly'])

    def test_private_sheet_rows_are_batched_and_empty_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            access=GoogleReadOnlyAccess(tmp)
            with patch.object(access,'access_token',return_value='synthetic'),patch.object(access,'_request',side_effect=[{'sheets':[{'properties':{'sheetId':8,'title':'Private'}}]},{'values':[['A','B'],['C','D']]}]) as request:
                self.assertEqual(access.csv_rows('fake-id','8',{'8':'Private'}),[['A','B'],['C','D']])
                self.assertIn('%21A1%3AZZ1500',request.call_args.args[0])

    def test_desktop_never_syncs_edits_to_google(self):
        source=(ROOT/'dashboard/app.py').read_text('utf-8')
        for func in ['google_sheets_request','google_sheets_values_update','google_sheets_values_batch_update',
                     'sync_vendor_control_cell','sync_import_cells_to_google_sheet','sync_rfq_cell_to_google_sheet']:
            self.assertIn('def '+func+'(',source)
        self.assertIn("if method.upper() != 'GET' or body is not None:",source)
        self.assertIn('Desktop Google Sheets is READ-ONLY',source)
        self.assertIn("'sheet_written': False",source)
        ui=(ROOT/'dashboard/static/desktop/sheet_sync.js').read_text('utf-8')
        self.assertIn('Google Login',ui)
        self.assertNotIn('Konfigurasi Google service account',ui)
        runtime=(ROOT/'desktop_runtime.py').read_text('utf-8')
        self.assertIn('GoogleReadOnlyAccess',runtime)
        self.assertNotIn('GoogleCredentialStore',runtime)
        self.assertNotIn('_sheets_edit_scope',runtime)

    def test_offline_error_preserves_existing_snapshot(self):
        from tests.test_v018_sheets_sync import FakeBackend,FakeApp,DummyCredentials,DummySnapshot,SheetSyncCoordinator
        from time import sleep, monotonic
        backend=FakeBackend();backend.fail_vendor=True
        store=DummySnapshot();store.rows=[{'vendor_name':'Offline previous'}]
        coordinator=SheetSyncCoordinator(DummyCredentials(True),store,backend,FakeApp())
        self.assertTrue(coordinator.start('vendor','once')['accepted'])
        deadline=monotonic()+3
        while monotonic()<deadline and coordinator.status()['vendor']['state']!='failed':sleep(.01)
        self.assertEqual(coordinator.status()['vendor']['state'],'failed')
        self.assertEqual(store.writes,0)
        self.assertEqual(store.rows,[{'vendor_name':'Offline previous'}])

if __name__=='__main__':unittest.main()
