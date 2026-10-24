from fastapi import APIRouter, Depends

from app.deps import current_user
from app.services import features, pr_impact

router = APIRouter(tags=["impact"])


# --- per repository ---------------------------------------------------------------------------------------------


@router.get("/repositories/{repo_id}/pull-requests")
def pull_requests(repo_id: str, user: dict = Depends(current_user)):
    return pr_impact.pull_requests(user, repo_id)


@router.get("/repositories/{repo_id}/refs")
def refs(repo_id: str, user: dict = Depends(current_user)):
    return pr_impact.refs(user, repo_id)


@router.post("/repositories/{repo_id}/impact-runs")
def start_run(repo_id: str, body: dict, user: dict = Depends(current_user)):
    return pr_impact.start_run(user, repo_id, body)


@router.get("/repositories/{repo_id}/features")
def list_features(repo_id: str, analysis_id: str = "", user: dict = Depends(current_user)):
    return features.list_features(user, repo_id, analysis_id)


@router.post("/repositories/{repo_id}/features")
def create_feature(repo_id: str, body: dict, user: dict = Depends(current_user)):
    return features.create_feature(user, repo_id, body)


@router.post("/repositories/{repo_id}/features/suggest")
def suggest_features(repo_id: str, body: dict | None = None, user: dict = Depends(current_user)):
    return features.suggest(user, repo_id, body or {})


# --- runs -------------------------------------------------------------------------------------------------------


@router.get("/impact-runs")
def list_runs(repo_id: str = "", limit: int = 100, user: dict = Depends(current_user)):
    return pr_impact.list_runs(user, repo_id, limit)


@router.get("/impact-runs/{run_id}")
def get_run(run_id: str, user: dict = Depends(current_user)):
    return pr_impact.get_run(user, run_id)


@router.get("/impact-runs/{run_id}/events")
def events(run_id: str, after: str = "", user: dict = Depends(current_user)):
    return pr_impact.events(user, run_id, after)


@router.get("/impact-runs/{run_id}/graph")
def graph(run_id: str, limit: int = 160, user: dict = Depends(current_user)):
    return pr_impact.impact_graph(user, run_id, limit)


@router.get("/impact-runs/{run_id}/diff")
def file_diff(run_id: str, path: str, user: dict = Depends(current_user)):
    return pr_impact.file_diff(user, run_id, path)


@router.post("/impact-runs/{run_id}/cancel")
def cancel(run_id: str, user: dict = Depends(current_user)):
    return pr_impact.cancel_run(user, run_id)


@router.post("/impact-runs/{run_id}/rerun")
def rerun(run_id: str, user: dict = Depends(current_user)):
    return pr_impact.rerun(user, run_id)


@router.post("/impact-runs/{run_id}/narrative")
def narrative(run_id: str, user: dict = Depends(current_user)):
    return pr_impact.regenerate_narrative(user, run_id)


@router.delete("/impact-runs/{run_id}", status_code=204)
def delete_run(run_id: str, user: dict = Depends(current_user)):
    pr_impact.delete_run(user, run_id)


# --- features ---------------------------------------------------------------------------------------------------


@router.patch("/features/{feature_id}")
def update_feature(feature_id: str, body: dict, user: dict = Depends(current_user)):
    return features.update_feature(user, feature_id, body)


@router.delete("/features/{feature_id}", status_code=204)
def delete_feature(feature_id: str, user: dict = Depends(current_user)):
    features.delete_feature(user, feature_id)
