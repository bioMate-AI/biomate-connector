"""OAuth-scoped access to the same Node tools used by personal API-key clients."""
import httpx
from starlette.responses import JSONResponse
from .credentials import get_api_key
from .local_tools import local_only_tool_names
from .oauth_app import _login_url, server
from .server import _TOOL_SCOPES

async def oauth_tool(request):
    authorization = request.headers.get("authorization", "")
    claims = server().verify_access(authorization[7:]) if authorization.startswith("Bearer ") else None
    if claims is None:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    name = request.path_params["name"]
    required = _TOOL_SCOPES.get(name)
    # This bridge proxies to the cloud backend, which cannot reach lab instruments.
    if required is None or name in local_only_tool_names():
        return JSONResponse({"error": "Unknown tool"}, status_code=404)
    if required not in claims.scopes:
        return JSONResponse({"error": "insufficient_scope", "required_scope": required}, status_code=403)
    key = get_api_key(claims.sub)
    if not key:
        return JSONResponse({"error": "Reconnect BioMate: personal credentials unavailable"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Tool arguments must be an object"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{_login_url()}/tools/{name}",
                headers={"Authorization": f"Bearer {key}"}, json=body)
        return JSONResponse(response.json(), status_code=response.status_code)
    except Exception:
        return JSONResponse({"error": "BioMate tool service unavailable"}, status_code=502)
