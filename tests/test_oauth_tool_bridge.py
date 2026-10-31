import os, sys, secrets
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remote_mcp.bootstrap
import pytest, httpx
from starlette.applications import Starlette
from starlette.routing import Route
from remote_mcp.tool_bridge import oauth_tool
from remote_mcp.credentials import put_api_key
from oauth_server.oauth.tokens import AccessTokenClaims, issue_access_token, now
from remote_mcp.identity import resolve_api_key

@pytest.fixture
def settings(tmp_path, monkeypatch):
 monkeypatch.setenv('BIOMATE_OAUTH_SIGNING_KEY', secrets.token_urlsafe(64))
 monkeypatch.setenv('BIOMATE_OAUTH_DB', str(tmp_path/'auth.db'))
 monkeypatch.setenv('BIOMATE_API_KEY', 'shared-key-must-never-be-used')

def bearer(user='user-a', scopes=frozenset({'runs:read'}), expired=False):
 return issue_access_token(AccessTokenClaims(sub=user,surface='test',scopes=scopes,client_id='test',iat=now()-100,exp=now()-1 if expired else now()+600,jti=secrets.token_hex(16)))

@pytest.mark.asyncio
async def test_rejects_missing_expired_wrong_scope_and_missing_personal_key(settings):
 app=Starlette(routes=[Route('/oauth/tools/{name}',oauth_tool,methods=['POST'])])
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
  for token,name,status in [(None,'get_run',401),(bearer(expired=True),'get_run',401),(bearer(),'run_workflow',403),(bearer(),'list_instruments',404),(bearer(),'get_run',401)]:
   r=await c.post('/oauth/tools/'+name,json={},headers={'Authorization':'Bearer '+token} if token else {})
   assert r.status_code==status
 assert resolve_api_key('user-a')==''

@pytest.mark.asyncio
async def test_forwards_only_callers_personal_key(settings,monkeypatch):
 import remote_mcp.tool_bridge as bridge
 put_api_key('user-a','bm_live_user_a',now());put_api_key('user-b','bm_live_user_b',now())
 real=httpx.AsyncClient; seen=[]
 class Backend:
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def post(self,url,headers,json):
   seen.append(headers['Authorization'])
   return httpx.Response(200,json={'status':'completed'})
 monkeypatch.setattr(bridge.httpx,'AsyncClient',lambda **kw:Backend())
 app=Starlette(routes=[Route('/oauth/tools/{name}',oauth_tool,methods=['POST'])])
 async with real(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
  for user in ['user-a','user-b']:
   r=await c.post('/oauth/tools/get_run',json={'run_id':'x'},headers={'Authorization':'Bearer '+bearer(user)})
   assert r.status_code==200
 assert seen==['Bearer bm_live_user_a','Bearer bm_live_user_b']
