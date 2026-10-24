"""Every test gets a fresh database. Tests run on DuckDB, and also on PostgreSQL when TEST_DATABASE_URL is set."""

import os
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ENGINES = ["duckdb"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


def _reset_postgres(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=ENGINES)
def engine(request, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANALYSIS_SYNC", "1")
    monkeypatch.setenv("ALLOW_LOCAL_PATHS", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    if request.param == "postgres":
        url = os.environ["TEST_DATABASE_URL"]
        _reset_postgres(url)
        monkeypatch.setenv("DB_ENGINE", "postgres")
        monkeypatch.setenv("DATABASE_URL", url)
    else:
        monkeypatch.setenv("DB_ENGINE", "duckdb")
        monkeypatch.delenv("DUCKDB_PATH", raising=False)
    from app import db

    db.reset()
    yield request.param
    db.reset()


@pytest.fixture
def client(engine):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def login(client, login_id="admin", password="AdminPass123!") -> dict:
    response = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return login(client)


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    """A small polyglot repository with a dependency cycle, a layer violation and a hardcoded secret."""
    root = tmp_path / "sample"
    files = {
        "README.md": "# Orders\n\nOrder service built with FastAPI and Kafka.\n",
        "requirements.txt": "fastapi==0.115\nsqlalchemy\npydantic\n",
        "app/__init__.py": "",
        "app/main.py": """
            from fastapi import FastAPI
            from app.api.orders import router

            app = FastAPI()
            app.include_router(router)

            if __name__ == "__main__":
                import uvicorn
                uvicorn.run(app)
        """,
        "app/api/__init__.py": "",
        "app/api/orders.py": """
            from fastapi import APIRouter, Depends
            from app.services.order_service import OrderService

            router = APIRouter()

            @router.get("/orders/{order_id}")
            def get_order(order_id: int, service: OrderService = Depends(OrderService)):
                if order_id < 0 or order_id > 10_000:
                    return None
                return service.find(order_id)
        """,
        "app/services/__init__.py": "",
        "app/services/order_service.py": """
            from app.data.order_repository import OrderRepository


            class OrderService:
                def __init__(self, repo: OrderRepository):
                    self.repo = repo

                def find(self, order_id):
                    order = self.repo.get(order_id)
                    if order and order.status == "open":
                        return order
                    return None
        """,
        "app/data/__init__.py": "",
        "app/data/order_repository.py": """
            from app.data.models import Order
            from app.api.orders import router  # upward dependency: data -> api

            PASSWORD = "s3cr3tP4ssw0rd!"


            class OrderRepository:
                def get(self, order_id):
                    return session.query(Order).filter(Order.id == order_id).first()

                def save(self, order):
                    session.add(order)
                    session.commit()
        """,
        "app/data/models.py": """
            from sqlalchemy.orm import DeclarativeBase, mapped_column


            class Base(DeclarativeBase):
                pass


            class Order(Base):
                __tablename__ = "orders"
                id = mapped_column()
                status = mapped_column()
        """,
        "app/util/a.py": "from app.util.b import helper_b\n\ndef helper_a():\n    return helper_b()\n",
        "app/util/b.py": "from app.util.a import helper_a\n\ndef helper_b():\n    return helper_a()\n",
        "web/package.json": '{"name": "orders-web", "dependencies": {"react": "18.3.1", "axios": "1.7.0"}}',
        "web/src/App.tsx": """
            import React, { useState } from 'react';
            import { fetchOrder } from './api/client';

            export function App() {
              const [order, setOrder] = useState(null);
              return <div className="app" onClick={() => fetchOrder(1).then(setOrder)}>{String(order)}</div>;
            }
        """,
        "web/src/api/client.ts": """
            import axios from 'axios';

            export async function fetchOrder(id: number) {
              const response = await axios.get(`/orders/${id}`);
              return response.data;
            }
        """,
        "tests/test_orders.py": "from app.services.order_service import OrderService\n\ndef test_find():\n    assert OrderService\n",
        "node_modules/ignored/index.js": "module.exports = 1;\n",
        "dist/bundle.js": "console.log(1)\n",
    }
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return root


class FakeAdapter:
    """Stands in for Gemini / Spirare: JSON for synthesis prompts, prose for chat, deterministic vectors."""

    provider = "spirare"

    class config:
        embedding_model = "fake-embed"

    calls: list[tuple[str, str]] = []

    def chat_configured(self):
        return True

    def embedding_configured(self):
        return True

    def complete(self, system, user, temperature):
        FakeAdapter.calls.append((system, user))
        if "Respond with JSON only" in system:
            if '"narrative"' in user:
                return '{"narrative": "## Summary\\nLeft and right differ."}'
            if '"business_purpose"' in user:
                return '{"business_purpose": "Manages customer orders.", "summary": "An order service.", "key_capabilities": ["Order lookup"], "architecture_notes": [], "risks": ["Cycle in util"]}'
            return '```json\n{"summary": "Handles orders.", "business_capability": "Ordering"}\n```'
        return "The order flow goes from `app/api/orders.py` to `OrderService.find`."

    def embed(self, text):
        return [float((sum(map(ord, text)) % 97) + 1), 1.0, float(len(text) % 13)]


@pytest.fixture
def fake_llm(monkeypatch):
    FakeAdapter.calls = []
    monkeypatch.setattr("app.services.llm_settings.active_adapter", lambda: FakeAdapter())
    return FakeAdapter
