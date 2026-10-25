from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response

from app.deps import current_user
from app.services import analyses, catalog, repositories, retrieval

router = APIRouter(prefix="/analyses", tags=["analyses"])


@router.get("")
def list_analyses(repo_id: str = "", status: str = "", limit: int = 100, include_pr: bool = False, user: dict = Depends(current_user)):
    return analyses.list_analyses(user, repo_id, status, limit, include_pr)


@router.get("/{analysis_id}")
def get_analysis(analysis_id: str, user: dict = Depends(current_user)):
    return analyses.get_analysis(user, analysis_id)


@router.get("/{analysis_id}/events")
def events(analysis_id: str, after: str = "", user: dict = Depends(current_user)):
    return analyses.events(user, analysis_id, after)


@router.post("/{analysis_id}/cancel")
def cancel(analysis_id: str, user: dict = Depends(current_user)):
    return analyses.cancel(user, analysis_id)


@router.post("/{analysis_id}/rerun")
def rerun(analysis_id: str, user: dict = Depends(current_user)):
    row = analyses.get_analysis(user, analysis_id)
    return repositories.start_analysis(user, row["repo_id"], {"branch": row["branch"]})


@router.delete("/{analysis_id}", status_code=204)
def delete(analysis_id: str, user: dict = Depends(current_user)):
    analyses.delete_analysis(user, analysis_id)
    retrieval.forget(analysis_id)


@router.get("/{analysis_id}/files")
def files(analysis_id: str, q: str = "", layer: str = "", role: str = "", language: str = "", container: str = "", sort: str = "pagerank",
          order: str = "desc", include_tests: bool = True, only_source: bool = True, limit: int = 200, offset: int = 0, user: dict = Depends(current_user)):
    return analyses.list_files(user, analysis_id, q=q, layer=layer, role=role, language=language, container=container, sort=sort, order=order,
                               include_tests=include_tests, only_source=only_source, limit=limit, offset=offset)


@router.get("/{analysis_id}/file")
def file_detail(analysis_id: str, path: str, user: dict = Depends(current_user)):
    return analyses.file_detail(user, analysis_id, path)


@router.get("/{analysis_id}/blast-radius")
def blast(analysis_id: str, path: str, user: dict = Depends(current_user)):
    return analyses.blast_radius(user, analysis_id, path)


@router.get("/{analysis_id}/tree")
def tree(analysis_id: str, user: dict = Depends(current_user)):
    return analyses.tree(user, analysis_id)


@router.get("/{analysis_id}/graph")
def graph(analysis_id: str, limit: int = 150, layers: str = "", kinds: str = "", include_tests: bool = False, focus: str = "", container: str = "", user: dict = Depends(current_user)):
    return analyses.graph(user, analysis_id, limit=limit, layers=layers, kinds=kinds, include_tests=include_tests, focus=focus, container=container)


@router.get("/{analysis_id}/concerns")
def concerns(analysis_id: str, severity: str = "", category: str = "", layers: str = "", user: dict = Depends(current_user)):
    return analyses.concerns(user, analysis_id, severity, category, layers)


@router.get("/{analysis_id}/concerns.md", response_class=PlainTextResponse)
def concerns_md(analysis_id: str, user: dict = Depends(current_user)):
    return PlainTextResponse(analyses.concerns_markdown(user, analysis_id), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="CONCERNS-{analysis_id[:8]}.md"'})


@router.get("/{analysis_id}/symbols")
def symbols(analysis_id: str, q: str = "", kind: str = "", limit: int = 100, user: dict = Depends(current_user)):
    return analyses.symbols(user, analysis_id, q, kind, limit)


@router.get("/{analysis_id}/search")
def search(analysis_id: str, q: str = "", user: dict = Depends(current_user)):
    return analyses.search(user, analysis_id, q)


@router.get("/{analysis_id}/dependencies")
def dependencies(analysis_id: str, user: dict = Depends(current_user)):
    return analyses.dependencies(user, analysis_id)


# --- layer catalogue ------------------------------------------------------------------------------------------------


@router.get("/{analysis_id}/catalog")
def catalog_summary(analysis_id: str, user: dict = Depends(current_user)):
    return catalog.summary(user, analysis_id)


@router.post("/{analysis_id}/catalog/rebuild")
def catalog_rebuild(analysis_id: str, user: dict = Depends(current_user)):
    return catalog.rebuild(user, analysis_id)


@router.get("/{analysis_id}/catalog/items")
def catalog_items(analysis_id: str, layer: str = "", kind: str = "", q: str = "", limit: int = 500, offset: int = 0, user: dict = Depends(current_user)):
    return catalog.items(user, analysis_id, layer, kind, q, limit, offset)


@router.get("/{analysis_id}/catalog/items/{item_id}")
def catalog_item(analysis_id: str, item_id: str, user: dict = Depends(current_user)):
    return catalog.item(user, analysis_id, item_id)


@router.get("/{analysis_id}/catalog/resolve")
def catalog_resolve(analysis_id: str, key: str, user: dict = Depends(current_user)):
    return catalog.resolve_key(user, analysis_id, key)


@router.get("/{analysis_id}/catalog/items/{item_id}/trace")
def catalog_trace(analysis_id: str, item_id: str, depth: int = 5, user: dict = Depends(current_user)):
    return catalog.trace(user, analysis_id, item_id, max(1, min(depth, 8)))


@router.get("/{analysis_id}/catalog/items/{item_id}/wireframe")
def catalog_item_wireframe(analysis_id: str, item_id: str, mode: str = "deterministic", regenerate: bool = False, user: dict = Depends(current_user)):
    return catalog.wireframe(user, analysis_id, item_id, mode, regenerate)


@router.get("/{analysis_id}/catalog/source")
def catalog_source(analysis_id: str, path: str, user: dict = Depends(current_user)):
    return catalog.source(user, analysis_id, path)


@router.get("/{analysis_id}/catalog/erd")
def catalog_erd(analysis_id: str, keys: str = "", user: dict = Depends(current_user)):
    return catalog.erd(user, analysis_id, keys)


@router.get("/{analysis_id}/catalog/crud")
def catalog_crud(analysis_id: str, user: dict = Depends(current_user)):
    return catalog.crud(user, analysis_id)


@router.get("/{analysis_id}/catalog/export.md", response_class=PlainTextResponse)
def catalog_markdown(analysis_id: str, user: dict = Depends(current_user)):
    return PlainTextResponse(catalog.export_markdown(user, analysis_id), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="layer-catalogue-{analysis_id[:8]}.md"'})


@router.get("/{analysis_id}/catalog/export.xlsx")
def catalog_excel(analysis_id: str, user: dict = Depends(current_user)):
    return Response(catalog.export_xlsx(user, analysis_id), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="layer-catalogue-{analysis_id[:8]}.xlsx"'})
