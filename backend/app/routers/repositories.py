from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.deps import current_user
from app.services import repositories

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("")
def list_repositories(user: dict = Depends(current_user)):
    return repositories.list_repositories(user)


@router.post("")
def create_repository(body: dict, user: dict = Depends(current_user)):
    if body.get("source_type") == "local":
        return repositories.create_local(user, body)
    return repositories.create_remote(user, body)


@router.post("/upload")
def upload_repository(file: UploadFile = File(...), name: str = Form(""), repo_id: str = Form(""), user: dict = Depends(current_user)):
    return repositories.create_upload(user, name, file.filename or "", file.file, repo_id or None)


@router.post("/upload-folder")
async def upload_folder_repository(request: Request, user: dict = Depends(current_user)):
    form = await request.form(max_files=100_000, max_fields=200_000)
    try:
        name = str(form.get("name") or "")
        repo_id = str(form.get("repo_id") or "")
        files = [item for item in form.getlist("files") if hasattr(item, "file")]
        paths = [str(item) for item in form.getlist("paths")]
        return repositories.create_folder_upload(user, name, files, paths, repo_id or None)
    finally:
        await form.close()


@router.get("/{repo_id}")
def get_repository(repo_id: str, user: dict = Depends(current_user)):
    return repositories.get_repository(user, repo_id)


@router.patch("/{repo_id}")
def update_repository(repo_id: str, body: dict, user: dict = Depends(current_user)):
    return repositories.update_repository(user, repo_id, body)


@router.delete("/{repo_id}", status_code=204)
def delete_repository(repo_id: str, user: dict = Depends(current_user)):
    repositories.delete_repository(user, repo_id)


@router.get("/{repo_id}/branches")
def branches(repo_id: str, user: dict = Depends(current_user)):
    return repositories.branches(user, repo_id)


@router.post("/{repo_id}/analyses")
def start_analysis(repo_id: str, body: dict, user: dict = Depends(current_user)):
    return repositories.start_analysis(user, repo_id, body)
