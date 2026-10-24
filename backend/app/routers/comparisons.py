from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response

from app.deps import current_user
from app.services import compare

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


@router.get("")
def list_comparisons(user: dict = Depends(current_user)):
    return compare.list_comparisons(user)


@router.get("/page")
def page_comparisons(page: int = 1, page_size: int = 50, date_from: str = "", date_to: str = "", q: str = "", user: dict = Depends(current_user)):
    return compare.page_comparisons(user, page, page_size, date_from, date_to, q)


@router.post("/purge")
def purge_comparisons(body: dict, user: dict = Depends(current_user)):
    return compare.purge_comparisons(user, body)


@router.post("")
def create_comparison(body: dict, user: dict = Depends(current_user)):
    return compare.create_comparison(user, body)


@router.get("/{comparison_id}")
def get_comparison(comparison_id: str, user: dict = Depends(current_user)):
    return compare.get_comparison(user, comparison_id)


@router.get("/{comparison_id}/report.md", response_class=PlainTextResponse)
def comparison_report_md(comparison_id: str, user: dict = Depends(current_user)):
    return PlainTextResponse(
        compare.comparison_markdown(user, comparison_id),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="COMPARISON-{comparison_id[:8]}.md"'},
    )


@router.get("/{comparison_id}/report.pdf")
def comparison_report_pdf(comparison_id: str, user: dict = Depends(current_user)):
    return Response(
        content=compare.comparison_pdf(user, comparison_id),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="COMPARISON-{comparison_id[:8]}.pdf"'},
    )


@router.post("/{comparison_id}/narrative")
def narrative(comparison_id: str, user: dict = Depends(current_user)):
    return compare.regenerate_narrative(user, comparison_id)


@router.delete("/{comparison_id}", status_code=204)
def delete_comparison(comparison_id: str, user: dict = Depends(current_user)):
    compare.delete_comparison(user, comparison_id)
