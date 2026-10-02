"""API 层测试：不启动 lifespan（不连 Redis/Chroma/LLM），只验证 HTTP 行为和可观测性接线。"""
import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_is_503_before_startup(client):
    assert client.get("/health").status_code == 503


def test_metrics_exposes_llm_and_http_series(client):
    client.get("/openapi.json")
    body = client.get("/metrics").text
    assert "companyagent_http_requests_total" in body
    assert 'route="/openapi.json"' in body
    assert "companyagent_llm_requests_total" in body


def test_response_carries_trace_id(client):
    resp = client.get("/openapi.json")
    assert len(resp.headers["X-Trace-Id"]) == 32


def test_admin_endpoints_require_token(client):
    assert client.get("/monitor").status_code == 401
