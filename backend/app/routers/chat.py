from fastapi import APIRouter, Depends

from app.deps import current_user
from app.services import chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/sessions")
def list_sessions(scope_type: str = "", scope_id: str = "", user: dict = Depends(current_user)):
    return chat.list_sessions(user, scope_type, scope_id)


@router.post("/sessions")
def create_session(body: dict, user: dict = Depends(current_user)):
    return chat.create_session(user, body)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: dict = Depends(current_user)):
    return chat.get_session(user, session_id)


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: dict, user: dict = Depends(current_user)):
    return chat.update_session(user, session_id, body)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user: dict = Depends(current_user)):
    chat.delete_session(user, session_id)


@router.post("/sessions/{session_id}/messages")
def ask(session_id: str, body: dict, user: dict = Depends(current_user)):
    return chat.ask(user, session_id, str(body.get("content") or ""))
