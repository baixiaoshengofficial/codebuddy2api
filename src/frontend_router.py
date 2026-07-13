import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

FRONTEND_DIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
)
HTML_FILE_PATH = os.path.join(FRONTEND_DIST_DIR, "index.html")


def serve_index():
    if not os.path.exists(HTML_FILE_PATH):
        raise HTTPException(
            status_code=503,
            detail="Frontend build not found. Run npm install && npm run build in frontend/.",
        )
    return FileResponse(
        HTML_FILE_PATH,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

@router.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    return serve_index()

@router.get("/admin", response_class=FileResponse, include_in_schema=False) 
async def serve_admin():
    return serve_index()


@router.get("/assets/{asset_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_asset(asset_path: str):
    assets_dir = os.path.join(FRONTEND_DIST_DIR, "assets")
    requested_path = os.path.abspath(os.path.join(assets_dir, asset_path))
    if not requested_path.startswith(os.path.abspath(assets_dir) + os.sep):
        raise HTTPException(status_code=404, detail="Asset not found")
    if not os.path.isfile(requested_path):
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(
        requested_path,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
