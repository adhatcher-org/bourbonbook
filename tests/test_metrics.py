from __future__ import annotations

from pathlib import Path

from tests.test_app import make_client, register


def test_metrics_endpoint_uses_prometheus_format_and_route_templates(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        register(client)
        client.get("/bottles/123", follow_redirects=False)
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "bourbonbook_http_requests_total" in response.text
    assert 'route="/bottles/{bottle_id}"' in response.text
    assert "/bottles/123" not in response.text
