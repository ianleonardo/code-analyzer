from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response

from app.deps import current_user
from app.services import landscape, openapi

router = APIRouter(prefix="/landscapes", tags=["landscapes"])


@router.get("")
def list_landscapes(user: dict = Depends(current_user)):
    return landscape.list_landscapes(user)


@router.post("")
def create_landscape(body: dict, user: dict = Depends(current_user)):
    return landscape.create_landscape(user, body)


@router.get("/{landscape_id}")
def get_landscape(landscape_id: str, user: dict = Depends(current_user)):
    return landscape.get_landscape(user, landscape_id)


@router.patch("/{landscape_id}")
def update_landscape(landscape_id: str, body: dict, user: dict = Depends(current_user)):
    return landscape.update_landscape(user, landscape_id, body)


@router.delete("/{landscape_id}", status_code=204)
def delete_landscape(landscape_id: str, user: dict = Depends(current_user)):
    landscape.delete_landscape(user, landscape_id)


@router.post("/{landscape_id}/recompute")
def recompute(landscape_id: str, user: dict = Depends(current_user)):
    return landscape.recompute_landscape(user, landscape_id)


@router.get("/{landscape_id}/links")
def links(landscape_id: str, kind: str = "", status: str = "", repo_id: str = "", feature: str = "", q: str = "", limit: int = 2000, user: dict = Depends(current_user)):
    return landscape.links(user, landscape_id, kind, status, repo_id, feature, q, limit)


@router.get("/{landscape_id}/review")
def review(landscape_id: str, user: dict = Depends(current_user)):
    return landscape.review(user, landscape_id)


@router.post("/{landscape_id}/decisions")
def decide(landscape_id: str, body: dict, user: dict = Depends(current_user)):
    return landscape.decide(user, landscape_id, body)


@router.post("/{landscape_id}/explain")
def explain(landscape_id: str, body: dict, user: dict = Depends(current_user)):
    return landscape.explain(user, landscape_id, str(body.get("group_key") or ""))


@router.get("/{landscape_id}/map")
def system_map(landscape_id: str, kinds: str = "", feature: str = "", include_review: bool = True, user: dict = Depends(current_user)):
    return landscape.system_map(user, landscape_id, kinds, feature, include_review)


@router.get("/{landscape_id}/interfaces")
def interfaces(landscape_id: str, user: dict = Depends(current_user)):
    return landscape.interfaces(user, landscape_id)


@router.get("/{landscape_id}/entry-points")
def entry_points(landscape_id: str, user: dict = Depends(current_user)):
    return landscape.entry_points(user, landscape_id)


@router.get("/{landscape_id}/journey")
def journey(landscape_id: str, repo_id: str, key: str, user: dict = Depends(current_user)):
    return landscape.journey(user, landscape_id, repo_id, key)


@router.get("/{landscape_id}/report.md", response_class=PlainTextResponse)
def report_md(landscape_id: str, user: dict = Depends(current_user)):
    return PlainTextResponse(landscape.report_markdown(user, landscape_id), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="LANDSCAPE-{landscape_id[:8]}.md"'})


@router.get("/{landscape_id}/report.pdf")
def report_pdf(landscape_id: str, user: dict = Depends(current_user)):
    return Response(landscape.report_pdf(user, landscape_id), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="LANDSCAPE-{landscape_id[:8]}.pdf"'})


@router.get("/{landscape_id}/export.xlsx")
def export_xlsx(landscape_id: str, user: dict = Depends(current_user)):
    return Response(landscape.export_xlsx(user, landscape_id), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="LANDSCAPE-{landscape_id[:8]}.xlsx"'})


@router.get("/{landscape_id}/external-openapi")
def external_openapi(landscape_id: str, name: str, version: str = "3.0.3", format: str = "yaml", user: dict = Depends(current_user)):
    return openapi.external_spec(user, landscape_id, name, version, format)


@router.get("/{landscape_id}/external-openapi/download")
def external_openapi_download(landscape_id: str, name: str, version: str = "3.0.3", format: str = "yaml", user: dict = Depends(current_user)):
    result = openapi.external_spec(user, landscape_id, name, version, format)
    return Response(result["text"].encode("utf-8"), media_type="application/json" if format == "json" else "application/yaml",
                    headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'})
