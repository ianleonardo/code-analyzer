import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import db
from app.audit_middleware import AuditMiddleware
from app.config import load_dotenv
from app.deps import enforce_access
from app.errors import AppError
from app.routers import admin, analyses, chat, comparisons, dashboard, git, repositories
from app.routers import auth as auth_routes
from app.services import jobs

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        load_dotenv()
    db.init()
    jobs.resume_after_restart()
    yield
    jobs.shutdown()
    db.reset()


app = FastAPI(title="Code Analyzer", lifespan=lifespan)
app.add_middleware(AuditMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(AppError)
def app_error_handler(_request, exc: AppError):
    body: dict = {"detail": exc.detail}
    if exc.errors:
        body["errors"] = exc.errors
    if exc.extra:
        body.update(exc.extra)
    return JSONResponse(body, status_code=exc.status)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": db.engine()}


app.include_router(auth_routes.router, prefix="/api")
app.include_router(git.public, prefix="/api")  # OAuth popup callback (state-signed, no bearer token)
# Every other route is checked against the user's role: area from the path, read or write from the method.
guarded = [Depends(enforce_access)]
for module in (dashboard, repositories, analyses, git, chat, comparisons, admin):
    app.include_router(module.router, prefix="/api", dependencies=guarded)


# In the container the built frontend is served from here, so one port serves the app and its API.
_frontend = Path(os.environ.get("FRONTEND_DIR", "")).resolve() if os.environ.get("FRONTEND_DIR") else None
if _frontend and (_frontend / "index.html").is_file():

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = (_frontend / path).resolve()
        if path and target.is_file() and _frontend in target.parents:
            return FileResponse(target)
        return FileResponse(_frontend / "index.html", headers={"Cache-Control": "no-cache"})
