"""On-demand READ-ONLY Google Sheets for the desktop.

Public/organization-approved link-accessible sheets need no credential. Private
sheets use a user Google OAuth installed-app client with only spreadsheets.readonly;
OAuth refresh tokens are encrypted with Windows DPAPI. No service-account private
key is accepted and no Google Sheets write API is called by this adapter.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
from threading import RLock, Thread, local
from urllib.parse import quote
from desktop_secrets import _crypt
from desktop_network_guard import permit_google_sheets_network

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
_ENTROPY_CLIENT=b'ServeoneDesktop.OAuthClient.ReadOnly.v1'
_ENTROPY_TOKEN=b'ServeoneDesktop.OAuthToken.ReadOnly.v1'
MAX_CSV_BYTES = 120 * 1024 * 1024
MAX_PRIVATE_ROWS = 150000
# Limit the Google Sheets API's large-sheet responses to reliable, bounded chunks.
PRIVATE_PAGE_ROWS = 1500
_ALLOWED_TOKEN_URI='https://oauth2.googleapis.com/token'
_ALLOWED_AUTH_URI='https://accounts.google.com/o/oauth2/auth'

class GoogleReadOnlyAccess:
    def __init__(self, folder):
        folder=Path(folder)
        self.client_path=folder/'google_oauth_client.dpapi'
        self.token_path=folder/'google_oauth_token.dpapi'
        self.lock=RLock()
        self.connecting=False
        self.connect_error=None
        self._tls=local()

    @staticmethod
    def validate_oauth_client(value):
        if not isinstance(value,dict) or 'installed' not in value:
            raise ValueError('Select an OAuth Desktop application client JSON, NOT a service account private key.')
        client=value['installed']
        if not isinstance(client,dict): raise ValueError('Invalid OAuth Desktop client')
        if not isinstance(client.get('client_id'),str) or not client['client_id'].endswith('.apps.googleusercontent.com'):
            raise ValueError('A Google OAuth Desktop application client_id is required')
        if client.get('auth_uri')!=_ALLOWED_AUTH_URI or client.get('token_uri')!=_ALLOWED_TOKEN_URI:
            raise ValueError('OAuth client endpoints must be official Google URLs')
        # Desktop-app OAuth JSON can contain client_secret. This is an OAuth
        # client identifier, NOT a Google service-account private key.
        if 'private_key' in json.dumps(value):raise ValueError('Private keys are not accepted')
        return {'installed':{k:client[k] for k in ('client_id','client_secret','auth_uri','token_uri','project_id') if k in client}}

    def status(self):
        return {'configured': True, 'source':'Read-only Google Sheets',
                'oauth_client_configured':self.client_path.is_file(),
                'oauth_connected':self.token_path.is_file(),
                'connecting':self.connecting,
                'connect_error':self.connect_error,
                'requires_service_account':False}

    @classmethod
    def desktop_client_from_fields(cls, client_id, client_secret=""):
        """Create an installed-app OAuth configuration from a small UI form.

        Only the OAuth app identifier/secret are accepted, NEVER service-account keys.
        """
        if not isinstance(client_id, str) or not isinstance(client_secret, str):
            raise ValueError("OAuth Desktop Client ID and Client Secret must be text")
        client_id=client_id.strip()
        client_secret=client_secret.strip()
        if not client_id or len(client_id)>512 or "\n" in client_id:
            raise ValueError("Enter a valid Google OAuth Desktop Client ID")
        if len(client_secret)>1024 or any(c in client_secret for c in ("\r","\n")):
            raise ValueError("Invalid OAuth Desktop Client Secret")
        return cls.validate_oauth_client({"installed": {
            "client_id":client_id, "client_secret":client_secret,
            "auth_uri":_ALLOWED_AUTH_URI, "token_uri":_ALLOWED_TOKEN_URI,
        }})

    def save(self,value):
        data=json.dumps(self.validate_oauth_client(value)).encode('utf-8')
        if len(data)>32768:raise ValueError('OAuth client configuration too large')
        self._atomic_store(self.client_path,data,_ENTROPY_CLIENT)
        # Never reuse a token from a previously configured Google app.
        self.token_path.unlink(missing_ok=True)
        self.connect_error=None
        return self.status()

    @staticmethod
    def _atomic_store(path,data,entropy):
        path.parent.mkdir(parents=True,exist_ok=True)
        blob=_crypt(data,purpose=entropy)
        staging=path.with_name(path.name+'.tmp')
        try:
            staging.write_bytes(blob)
            os.replace(staging,path)
        finally: staging.unlink(missing_ok=True)

    @staticmethod
    def _load_store(path,entropy):
        return json.loads(_crypt(path.read_bytes(),True,entropy).decode('utf-8'))

    def connect(self):
        if not self.client_path.is_file():raise RuntimeError('Configure an OAuth Desktop client first')
        with self.lock:
            if self.connecting:return False
            self.connecting=True
            self.connect_error=None
        Thread(target=self._connect_worker,daemon=True,name='google-oauth-readonly-login').start()
        return True

    def _connect_worker(self):
        try:
            # Authorize only Google endpoints from this thread. Google's own
            # sign-in page opens in the user's default external browser.
            with permit_google_sheets_network():
                from google_auth_oauthlib.flow import InstalledAppFlow
                client=self._load_store(self.client_path,_ENTROPY_CLIENT)
                flow=InstalledAppFlow.from_client_config(client,scopes=SCOPES)
                creds=flow.run_local_server(host='127.0.0.1',port=0,open_browser=True,
                        timeout_seconds=180,authorization_prompt_message='Google read-only login: {url}',
                        success_message='Google read-only connected. Return to Serveone Dashboard.')
                if not creds.refresh_token:
                    raise RuntimeError('No offline refresh token was issued. Revoke the old consent and connect again.')
                self._atomic_store(self.token_path,creds.to_json().encode('utf-8'),_ENTROPY_TOKEN)
        except Exception as exc:
            # Never put OAuth secrets or the full exception request URL in a UI.
            with self.lock:self.connect_error=(type(exc).__name__+': login failed or timed out')[:150]
        finally:
            with self.lock:self.connecting=False

    def disconnect(self):
        with self.lock:
            self.token_path.unlink(missing_ok=True)
            self.connect_error=None
        return self.status()

    def access_token(self):
        if not self.token_path.is_file():return None
        with self.lock:
            try:
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request
                info=self._load_store(self.token_path,_ENTROPY_TOKEN)
                creds=Credentials.from_authorized_user_info(info,scopes=SCOPES)
                if not creds.valid:
                    with permit_google_sheets_network():creds.refresh(Request())
                    self._atomic_store(self.token_path,creds.to_json().encode('utf-8'),_ENTROPY_TOKEN)
                return creds.token
            except Exception as exc:
                raise RuntimeError('Google read-only login expired or cannot be opened. Reconnect Google.') from exc

    def _request(self,url,params=None,token=None):
        import requests
        headers={'Accept':'application/json' if 'googleapis.com/v4/' in url else 'text/csv'}
        if token:headers['Authorization']='Bearer '+token
        try:
            response=requests.get(url,params=params,headers=headers,timeout=(15,90),allow_redirects=True,stream=True)
            response.raise_for_status()
            if 'sheets.googleapis.com' in url:
                result=response.json()
                response.close()
                return result
            collected=bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                collected.extend(chunk)
                if len(collected)>MAX_CSV_BYTES:
                    raise RuntimeError('Google CSV export is too large for a single read')
            content_type=(response.headers.get('content-type') or '').lower()
            response.close()
            data=bytes(collected)
            if (not data or data[:150].lstrip().lower().startswith((b'<!doctype',b'<html',b'<script'))
                    or ('text/html' in content_type)):
                raise PermissionError('Google Sheets returned a login or HTML page, not CSV')
            return data
        except requests.HTTPError as exc:
            code=exc.response.status_code if exc.response is not None else 'unknown'
            if code in (401,403,404):
                raise PermissionError('Google Sheet not accessible without login. Connect Google (read-only) for private sheets; do not make confidential Vendor sheets public.') from exc
            raise RuntimeError('Google Sheets request failed (HTTP '+str(code)+')') from exc
        except requests.RequestException as exc:
            raise RuntimeError('Google Sheets network unavailable; saved offline data is preserved.') from exc

    def metadata(self,spreadsheet_id,known_tabs):
        token=self.access_token()
        if token:
            return self._request('https://sheets.googleapis.com/v4/spreadsheets/'+quote(spreadsheet_id,safe=''),
                                 {'fields':'sheets.properties(sheetId,title)'},token)
        return {'sheets':[{'properties':{'sheetId':int(gid),'title':title}} for gid,title in known_tabs.items()]}

    def values(self,spreadsheet_id,a1,known_tabs,value_render_option='FORMATTED_VALUE',major_dimension='ROWS'):
        token=self.access_token()
        if token:
            url='https://sheets.googleapis.com/v4/spreadsheets/'+quote(spreadsheet_id,safe='')+'/values/'+quote(a1,safe='')
            return self._request(url,{'valueRenderOption':value_render_option,'majorDimension':major_dimension},token)
        title,left,right=parse_a1(a1)
        gids={title_:str(gid) for gid,title_ in known_tabs.items()}
        if title not in gids:raise ValueError('Google worksheet not configured for public CSV reader')
        grid=self.csv_rows(spreadsheet_id,gids[title])
        values=slice_a1_rows(grid,left,right)
        if major_dimension=='COLUMNS':values=[list(x) for x in __import__('itertools').zip_longest(*values,fillvalue='')] if values else []
        return {'range':a1,'majorDimension':major_dimension,'values':values}

    def csv_rows(self,spreadsheet_id,gid,known_tabs=None):
        # OAuth clients can read PRIVATE files with a read-only scoped token.
        # No service-account key, write scope or Sheets mutation is involved.
        token=self.access_token()
        if token:
            if not known_tabs:
                raise ValueError('Known sheet metadata required for private CSV replacement')
            # Private worksheets MUST use Google's real tab title. Public
            # CSV-only aliases are not necessarily the real A1 sheet titles.
            meta=self.metadata(spreadsheet_id,known_tabs)
            title=next((s['properties']['title'] for s in meta.get('sheets', [])
                        if str(s['properties']['sheetId'])==str(gid)),None)
            if title is None: raise RuntimeError('Google worksheet gid not found')
            return self._private_rows(spreadsheet_id,title,token)
        url='https://docs.google.com/spreadsheets/d/'+quote(spreadsheet_id,safe='')+'/export'
        payload=self._request(url,{'format':'csv','gid':str(gid)})
        try:text=payload.decode('utf-8-sig')
        except UnicodeDecodeError as exc:raise RuntimeError('Google CSV export was not UTF-8') from exc
        return list(csv.reader(StringIO(text,newline='')))

    def _private_rows(self,spreadsheet_id,title,token):
        result=[]
        for offset in range(1,MAX_PRIVATE_ROWS+1,PRIVATE_PAGE_ROWS):
            final=min(offset+PRIVATE_PAGE_ROWS-1,MAX_PRIVATE_ROWS)
            name="'"+title.replace("'","''")+"'!A"+str(offset)+':ZZ'+str(final)
            url='https://sheets.googleapis.com/v4/spreadsheets/'+quote(spreadsheet_id,safe='')+'/values/'+quote(name,safe='')
            part=self._request(url,{'valueRenderOption':'FORMATTED_VALUE','majorDimension':'ROWS'},token).get('values') or []
            result.extend(part)
            if len(part)<PRIVATE_PAGE_ROWS:break
        else: raise RuntimeError('Private Google Sheet has more rows than the configured safety limit')
        return result


def column_idx(letters):
    total=0
    for c in letters.upper():total=total*26+ord(c)-64
    return total-1

def parse_a1(a1):
    import re
    match=re.fullmatch(r"(?:'((?:[^']|'')+)'|([^!]+))!([A-Z]+\d*):([A-Z]+\d*)",a1,re.I)
    if not match:raise ValueError('Unsupported Google Sheets A1 range')
    title=(match.group(1) or match.group(2)).replace("''","'")
    return title,match.group(3).upper(),match.group(4).upper()

def slice_a1_rows(grid,start,end):
    import re
    a=re.fullmatch(r'([A-Z]+)(\d*)',start)
    b=re.fullmatch(r'([A-Z]+)(\d*)',end)
    r0=int(a[2] or 1)-1
    r1=int(b[2]) if b[2] else len(grid)
    c0=column_idx(a[1]);c1=column_idx(b[1])+1
    result=[]
    for row in grid[r0:r1]:
        values=list(row[c0:c1]);
        while values and values[-1]=='':values.pop()
        result.append(values)
    while result and not result[-1]:result.pop()
    return result
