"""In-process HTTP bridge for real browser flows without a listening socket."""

import base64
import json
import os
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import storage as db
from backend.app import app
from backend.manage import init_admin
from tests.fixtures import demo_files


async def mock_model(client, url, **kwargs):
    """Opt-in provider fixture for browser configuration tests, never production."""
    payload = kwargs["json"]
    if payload["model"] == "unavailable-model":
        raise httpx.ReadTimeout("browser provider fixture unavailable")
    name = payload["response_format"]["json_schema"]["name"]
    if name == "model-connection-test-v1":
        content = {"ok": True}
    elif name == "plan-review-v1":
        content = {"matches": True, "clarification": ""}
    else:
        evidence = json.loads(payload["messages"][1]["content"])
        content = {"evidence_id": evidence["evidence_id"], "actions": ["monitor"]}
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
            ],
            "usage": {"total_tokens": 12},
        },
    )


with tempfile.TemporaryDirectory() as folder:
    db.DB_PATH = Path(folder) / "browser.db"
    init_admin("owner", "测试负责人", "Owner-password-123")
    u = db.get("users", "owner")
    u.update(
        roles=["admin", "analyst", "manager"], stores=["SH01", "HZ01", "SZ01", "GZ01"]
    )
    db.put("users", u)
    print(json.dumps({"ready": True, "files": demo_files()}), flush=True)
    model_fixture = (
        patch("httpx.AsyncClient.post", mock_model)
        if os.getenv("RETAILPILOT_BROWSER_MOCK_MODEL") == "1"
        else nullcontext()
    )
    with model_fixture, TestClient(app, base_url="http://retailpilot.test") as client:
        for line in sys.stdin:
            data = json.loads(line)
            try:
                response = client.request(
                    data["method"],
                    data["path"],
                    content=data.get("body") or None,
                    headers=data.get("headers") or {},
                )
                print(
                    json.dumps(
                        {
                            "id": data["id"],
                            "status": response.status_code,
                            "headers": dict(response.headers),
                            "body": base64.b64encode(response.content).decode(),
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    json.dumps({"id": data["id"], "error": type(exc).__name__}),
                    flush=True,
                )
