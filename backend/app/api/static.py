"""Offline-built frontend static files with client-side route fallback."""

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path == "index.html":
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and path != "index.html":
            return await super().get_response("index.html", scope)
        return response
