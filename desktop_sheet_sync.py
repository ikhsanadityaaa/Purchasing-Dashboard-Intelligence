"""Desktop Google Sheets on-demand sync; offline-first and Windows DPAPI encrypted.

Never syncs at startup or by page GET. The UI requests the FIRST sync once per
app session and provides a separate explicit Sync action. Each dataset has an
independent background thread; cached data remains readable throughout sync.
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from desktop_secrets import _crypt
from desktop_network_guard import permit_google_sheets_network

_VENDOR_ENTROPY = b'ServeoneDesktop.VendorOfflineSnapshot.v1'


class VendorSnapshotStore:
    """A complete encrypted local snapshot, including vendor login passwords."""
    def __init__(self,path):
        self.path=Path(path)
        self.lock=threading.RLock()
    def load(self):
        with self.lock:
            if not self.path.is_file():return [],None
            try:
                raw=json.loads(_crypt(self.path.read_bytes(),True,_VENDOR_ENTROPY).decode('utf-8'))
                fetched=raw.get('fetched_at')
                return raw.get('rows') or [],datetime.fromisoformat(fetched) if fetched else None
            except Exception as exc:
                raise RuntimeError('Saved Vendor snapshot could not be opened by this Windows user') from exc
    def save(self,rows,fetched_at=None):
        now=fetched_at or datetime.now(timezone.utc).replace(tzinfo=None)
        data=json.dumps({'rows':rows,'fetched_at':now.isoformat()},ensure_ascii=False).encode('utf-8')
        encrypted=_crypt(data,purpose=_VENDOR_ENTROPY)
        with self.lock:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_name(self.path.name+'.tmp')
            try:
                tmp.write_bytes(encrypted)
                os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
    def edit(self,row_key,field,value):
        if field not in ('vendor_id','password'):raise ValueError('Vendor field is not editable')
        with self.lock:
            rows,fetched=self.load()
            for row in rows:
                if str(row.get('row_key'))==str(row_key):
                    row[field]=value
                    self.save(rows,fetched)
                    return True
        return False

class SheetSyncCoordinator:
    VALID=('import','vendor')
    def __init__(self,credentials,snapshot,backend,flask_app):
        self.credentials,self.snapshot,self.backend,self.app=credentials,snapshot,backend,flask_app
        self.lock=threading.RLock()
        self.auto_attempted=set()
        self.state={kind:{'state':'idle','last_success':None,'error':None,'stats':{}} for kind in self.VALID}
    def status(self):
        with self.lock:
            result={key:dict(value) for key,value in self.state.items()}
        result['credentials']=self.credentials.status()
        result['vendor_offline_available']=self.snapshot.path.is_file()
        return result
    def start(self,kind,mode='once'):
        if kind not in self.VALID:raise ValueError('Unknown sync page')
        if mode not in ('once','manual'):raise ValueError('Unknown sync mode')
        with self.lock:
            if mode=='once' and kind in self.auto_attempted:return {'accepted':False,'reason':'already_attempted','status':self.status()}
            if self.state[kind]['state']=='syncing':return {'accepted':False,'reason':'already_running','status':self.status()}
            if mode=='once':self.auto_attempted.add(kind)
            self.state[kind].update(state='syncing',error=None)
        threading.Thread(target=self._work,args=(kind,),name='google-sheet-sync-'+kind,daemon=True).start()
        return {'accepted':True,'status':self.status()}
    def _work(self,kind):
        try:
            with self.app.app_context(),permit_google_sheets_network():
                if kind=='import':
                    result=self.backend.sync_import_sheet_to_dashboard()
                    if not isinstance(result,dict) or result.get('error'):
                        raise RuntimeError(str((result or {}).get('error') or 'Import sync failed'))
                    stats={key:result.get(key,0) for key in ('added','updated','seen','sheet_rows','source_rows_added')}
                    if result.get('source_errors') or result.get('warning'):
                        stats['warning']='Some source sheets were unavailable; existing offline data was preserved.'
                else:
                    rows,fetched=self.backend.vendor_control_rows(force=True)
                    if not rows and self.snapshot.path.is_file():
                        raise RuntimeError('Vendor sheet unexpectedly returned no usable rows; keeping the saved offline snapshot.')
                    self.snapshot.save(rows,fetched)
                    stats={'rows':len(rows)}
                self.backend.clear_runtime_caches()
                self.backend._publish_data_revision()
            with self.lock:
                state='partial' if stats.get('warning') else 'completed'
                self.state[kind].update(state=state,last_success=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z'),stats=stats,error=None)
        except Exception as exc:
            try:
                with self.app.app_context():self.backend.db.session.rollback()
            except Exception:pass
            with self.lock:self.state[kind].update(state='failed',error=str(exc)[:300])
