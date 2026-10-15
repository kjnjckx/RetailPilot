"""Web configuration regression tests with isolated storage and mocked providers."""

import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import storage as db
from backend.app import app
from backend.domain import DomainError
from backend.manage import backup, init_admin, restore
from backend.model_adapter import review_plan
from tests import test_api

HEADERS = {"X-Requested-With": "RetailPilot"}
TEST_KEY = "web-test-secret-never-returned"


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "model.db"
        self.env = patch.dict(
            os.environ,
            {
                "RETAILPILOT_MODEL_PROVIDER": "rules",
                "RETAILPILOT_MODEL_BASE_URL": "https://api.openai.com/v1",
                "RETAILPILOT_MODEL_NAME": "",
                "RETAILPILOT_MODEL_API_KEY": "",
                "RETAILPILOT_MODEL_MAX_OUTPUT": "800",
                "RETAILPILOT_MODEL_DAILY_BUDGET": "100000",
            },
        )
        self.env.start()
        init_admin("owner", "配置管理员", "Owner-password-123")
        owner = db.get("users", "owner")
        db.put(
            "users",
            {**owner, "id": "analyst", "roles": ["analyst"], "stores": []},
        )
        self.client = TestClient(app, headers=HEADERS)
        self.client.__enter__()
        self.login()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DB_PATH = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def login(self, name="owner"):
        response = self.client.post(
            "/api/login",
            json={"username": name, "password": "Owner-password-123"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def config(self):
        response = self.client.get("/api/model-config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(TEST_KEY, response.text)
        return response.json()

    def payload(self, **updates):
        value = {
            "provider": "openai-compatible",
            "base_url": "https://api.openai.com/v1",
            "model_name": "web-model",
            "api_key": TEST_KEY,
            "clear_api_key": False,
            "max_output": 800,
            "daily_budget": 100000,
            "revision": self.config()["revision"],
        }
        return {**value, **updates}

    def save(self, payload=None, status=200, **updates):
        response = self.client.post(
            "/api/model-config", json=payload or self.payload(**updates)
        )
        self.assertEqual(response.status_code, status, response.text)
        self.assertNotIn(TEST_KEY, response.text)
        return response.json()

    @staticmethod
    def response(url, content, usage=None, finish="stop"):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": usage or {"total_tokens": 12},
            },
        )

    def test_admin_only_and_csrf_on_configuration_and_test(self):
        self.login("analyst")
        self.assertEqual(self.client.get("/api/model-config").status_code, 403)
        for endpoint in ["/api/model-config", "/api/model-config/test"]:
            with patch("httpx.AsyncClient.post") as remote:
                self.assertEqual(self.client.post(endpoint, json={}).status_code, 403)
                remote.assert_not_called()
        self.client.post("/api/logout", json={})
        self.assertEqual(self.client.get("/api/model-config").status_code, 401)
        for endpoint in ["/api/model-config", "/api/model-config/test"]:
            self.assertEqual(self.client.post(endpoint, json={}).status_code, 401)
        self.login()
        self.assertEqual(
            self.client.post(
                "/api/model-config",
                json=self.payload(),
                headers={"Origin": "https://other.example"},
            ).status_code,
            403,
        )

    def test_save_encrypted_key_safe_public_response_and_restart(self):
        first = self.config()
        self.assertEqual(first["provider"], "rules")
        self.assertFalse(first["has_api_key"])
        saved = self.save()
        self.assertEqual(saved["source"], "database")
        self.assertTrue(saved["enabled"])
        self.assertTrue(saved["has_api_key"])
        self.assertGreater(saved["revision"], first["revision"])
        self.assertNotIn("api_key", saved)
        key_path = db.DB_PATH.with_name(db.DB_PATH.name + ".model-key")
        self.assertTrue(key_path.is_file())
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        with db.connection() as con:
            dump = "\n".join(con.iterdump())
        self.assertNotIn(TEST_KEY, dump)
        self.assertNotIn(TEST_KEY, key_path.read_text())
        self.assertNotIn(TEST_KEY, self.client.get("/api/audit").text)
        self.assertNotIn(TEST_KEY, self.client.get("/api/model-status").text)
        db.initialize()
        self.assertEqual(self.config()["revision"], saved["revision"])

        async def post(client, url, **kwargs):
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TEST_KEY)
            self.assertEqual(kwargs["json"]["model"], "web-model")
            return self.response(url, {"matches": True, "clarification": ""})

        with patch("httpx.AsyncClient.post", post):
            meta = asyncio.run(
                review_plan({"question": "测试", "plan": {}, "scope": []}, {})
            )
        self.assertEqual(meta["model"], "web-model")

    def test_blank_key_preserves_replacement_and_clear_are_explicit(self):
        self.save()
        saved = self.save(api_key="", model_name="updated-model")
        self.assertTrue(saved["has_api_key"])
        saved = self.save(api_key="replacement-test-secret")
        self.assertTrue(saved["has_api_key"])
        saved = self.save(provider="rules", api_key="", clear_api_key=True)
        self.assertFalse(saved["enabled"])
        self.assertFalse(saved["has_api_key"])
        self.save(api_key="", status=400)

    def test_web_rules_override_environment_without_restart(self):
        with patch.dict(
            os.environ,
            {
                "RETAILPILOT_MODEL_PROVIDER": "openai-compatible",
                "RETAILPILOT_MODEL_NAME": "env-model",
                "RETAILPILOT_MODEL_API_KEY": TEST_KEY,
            },
        ):
            self.assertTrue(self.config()["enabled"])
            self.assertEqual(self.config()["source"], "environment")
            self.save(provider="rules", api_key="")
            self.assertFalse(self.client.get("/api/model-status").json()["enabled"])
            self.assertEqual(self.config()["source"], "database")
            meta = asyncio.run(review_plan({}, {}))
            self.assertEqual(meta["provider"], "rules")

    def test_invalid_legacy_environment_can_be_replaced_in_webpage(self):
        with patch.dict(
            os.environ,
            {
                "RETAILPILOT_MODEL_PROVIDER": "openai-compatible",
                "RETAILPILOT_MODEL_MAX_OUTPUT": "invalid-value",
                "RETAILPILOT_MODEL_DAILY_BUDGET": "invalid-budget",
            },
        ):
            self.config()
            saved = self.save()
            self.assertTrue(saved["enabled"])

    def test_concurrent_first_saves_accept_only_one_revision(self):
        from backend.model_config import save_config

        payload = self.payload()
        actor = db.get("users", "owner")

        def submit(index):
            try:
                return save_config({**payload, "model_name": f"model-{index}"}, actor)
            except DomainError as exc:
                return exc.status

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn(409, results)
        self.assertEqual(self.config()["revision"], payload["revision"] + 1)

    def test_stale_revision_does_not_overwrite_or_test_new_credentials(self):
        first = self.payload()
        self.save(first)
        self.save(first, status=409)
        with patch("httpx.AsyncClient.post") as remote:
            response = self.client.post("/api/model-config/test", json=first)
            self.assertEqual(response.status_code, 409, response.text)
            remote.assert_not_called()
        self.assertEqual(self.config()["model_name"], "web-model")

    def test_endpoint_change_cannot_send_retained_key(self):
        self.save()
        payload = self.payload(base_url="https://models.example/v1", api_key="")
        self.save(payload, status=400)
        with patch("httpx.AsyncClient.post") as remote:
            response = self.client.post("/api/model-config/test", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
            remote.assert_not_called()
        self.assertEqual(self.config()["base_url"], "https://api.openai.com/v1")
        self.save(base_url="https://models.example/v1", api_key="new-provider-secret")

    def test_strict_contract_and_unsafe_url_validation(self):
        for updates in [
            {"provider": "arbitrary"},
            {"base_url": "http://remote.example/v1"},
            {"base_url": "https://user:password@remote.example/v1"},
            {"base_url": "https://remote.example/v1?key=" + TEST_KEY},
            {"base_url": "https://remote.example/v1#fragment"},
            {"base_url": "https://remote.example/\n"},
            {"model_name": ""},
            {"max_output": 99},
            {"max_output": 2001},
            {"max_output": "800"},
            {"daily_budget": 0},
            {"clear_api_key": "false"},
            {"unknown": "field"},
        ]:
            with self.subTest(updates=updates):
                self.save(status=400, **updates)
        self.assertFalse(self.config()["enabled"])

    def test_local_http_service_and_trailing_slash_normalization(self):
        saved = self.save(base_url="http://127.0.0.1:11434/v1/")
        self.assertEqual(saved["base_url"], "http://127.0.0.1:11434/v1")
        self.save(base_url="http://127.0.0.1:11434/v1", api_key="")

    def test_connection_test_uses_draft_and_does_not_activate_or_save(self):
        before = self.config()
        seen = []

        async def post(client, url, **kwargs):
            seen.append(kwargs["json"])
            self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
            self.assertEqual(kwargs["json"]["model"], "draft-model")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TEST_KEY)
            self.assertTrue(kwargs["json"]["response_format"]["json_schema"]["strict"])
            return self.response(url, {"ok": True}, {"total_tokens": 12, "echo": TEST_KEY})

        with patch("httpx.AsyncClient.post", post):
            response = self.client.post(
                "/api/model-config/test", json=self.payload(model_name="draft-model")
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["model_name"], "draft-model")
        self.assertNotIn(TEST_KEY, response.text)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("经营", json.dumps(seen))
        self.assertEqual(self.config(), before)
        self.assertFalse(self.client.get("/api/model-status").json()["enabled"])
        with db.connection() as con:
            reserved = con.execute("SELECT reserved FROM model_budget").fetchone()[0]
        self.assertGreater(reserved, 0)
        self.assertNotIn(TEST_KEY, self.client.get("/api/audit").text)

    def test_failed_connection_never_leaks_upstream_and_leaves_config_unchanged(self):
        self.save()
        before = self.config()
        for content, finish in [
            ({"ok": False}, "stop"),
            ({"ok": True, "secret": TEST_KEY}, "stop"),
            ({"ok": True}, "length"),
        ]:

            async def post(client, url, **kwargs):
                return self.response(url, content, finish=finish)

            with patch("httpx.AsyncClient.post", post):
                response = self.client.post(
                    "/api/model-config/test", json=self.payload(api_key="")
                )
            self.assertNotEqual(response.status_code, 200, response.text)
            self.assertNotIn(TEST_KEY, response.text)
            self.assertEqual(self.config(), before)

        async def timeout(client, url, **kwargs):
            raise httpx.ReadTimeout(TEST_KEY)

        with patch("httpx.AsyncClient.post", timeout):
            response = self.client.post(
                "/api/model-config/test", json=self.payload(api_key="")
            )
        self.assertNotEqual(response.status_code, 200, response.text)
        self.assertNotIn(TEST_KEY, response.text)
        self.assertNotIn(TEST_KEY, self.client.get("/api/audit").text)

    def test_paired_backup_and_restore_to_another_database(self):
        self.save()
        destination = Path(self.tmp.name) / "backup.db"
        backup(destination)
        sidecar = destination.with_name(destination.name + ".model-key")
        self.assertTrue(sidecar.is_file())
        self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        original = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "restored.db"
        try:
            restore(destination, stopped=True)

            async def post(client, url, **kwargs):
                self.assertEqual(
                    kwargs["headers"]["Authorization"], "Bearer " + TEST_KEY
                )
                return self.response(url, {"matches": True, "clarification": ""})

            with patch("httpx.AsyncClient.post", post):
                meta = asyncio.run(
                    review_plan({"question": "测试", "plan": {}, "scope": []}, {})
                )
            self.assertEqual(meta["model"], "web-model")
        finally:
            db.DB_PATH = original

    def test_missing_key_does_not_regenerate_or_expose_secret(self):
        self.save()
        key_path = db.DB_PATH.with_name(db.DB_PATH.name + ".model-key")
        key_path.unlink()
        self.assertTrue(self.config()["has_api_key"])
        with self.assertRaises(DomainError):
            asyncio.run(
                review_plan({"question": "测试", "plan": {}, "scope": []}, {})
            )
        self.assertFalse(key_path.exists())
        self.save(provider="rules", api_key="", clear_api_key=True)
        self.assertFalse(self.config()["enabled"])
        self.save(api_key="replacement-key-after-loss")
        self.assertTrue(key_path.is_file())

    def test_corrupted_sidecar_can_be_explicitly_cleared_and_replaced(self):
        self.save()
        key_path = db.DB_PATH.with_name(db.DB_PATH.name + ".model-key")
        key_path.write_bytes(b"invalid-encryption-material")
        self.assertTrue(self.config()["has_api_key"])
        with self.assertRaises(DomainError):
            asyncio.run(
                review_plan({"question": "test", "plan": {}, "scope": []}, {})
            )
        self.save(provider="rules", api_key="", clear_api_key=True)
        self.save(api_key="fresh-key-after-explicit-clear")
        self.assertTrue(self.config()["enabled"])

    def test_backup_restore_refuse_missing_or_mismatched_paired_keys(self):
        self.save()
        destination = Path(self.tmp.name) / "paired.db"
        backup(destination)
        pair = destination.with_name(destination.name + ".model-key")
        pair.unlink()
        with self.assertRaises(ValueError):
            restore(destination, stopped=True)
        self.assertEqual(self.config()["model_name"], "web-model")
        key_path = db.DB_PATH.with_name(db.DB_PATH.name + ".model-key")
        key_path.unlink()
        incomplete = Path(self.tmp.name) / "incomplete.db"
        with self.assertRaises(ValueError):
            backup(incomplete)
        self.assertFalse(incomplete.exists())


class ModelNetworkGuardTests(unittest.TestCase):
    def test_real_transport_pins_public_dns_and_preserves_host_and_tls_name(self):
        from backend.model_adapter import GuardedTransport

        seen = []

        async def send(transport, request):
            seen.append(request)
            return httpx.Response(200, json={})

        async def run():
            async with GuardedTransport() as transport:
                return await transport.handle_async_request(
                    httpx.Request("POST", "https://models.example/v1/chat/completions")
                )

        with (
            patch(
                "backend.model_adapter.resolve_addresses",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
            patch("httpx.AsyncHTTPTransport.handle_async_request", send),
        ):
            asyncio.run(run())
        self.assertEqual(seen[0].url.host, "8.8.8.8")
        self.assertEqual(seen[0].headers["host"], "models.example")
        self.assertEqual(seen[0].extensions["sni_hostname"], "models.example")

    def test_private_dns_destination_rejected_before_any_connection(self):
        from backend.model_adapter import GuardedTransport

        async def run():
            async with GuardedTransport() as transport:
                await transport.handle_async_request(
                    httpx.Request("POST", "https://models.example/v1/chat/completions")
                )

        for address in ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"]:
            with (
                self.subTest(address=address),
                patch(
                    "backend.model_adapter.resolve_addresses",
                    return_value=[(2, 1, 6, "", (address, 443))],
                ),
                patch("httpx.AsyncHTTPTransport.handle_async_request") as network,
            ):
                with self.assertRaises(DomainError):
                    asyncio.run(run())
                network.assert_not_called()


class SavedModelWorkflowTests(unittest.TestCase):
    """Verify saved web settings feed the two existing analysis model stages."""

    seed_fixture = test_api.ApiWorkflowTests.seed_fixture
    login = test_api.ApiWorkflowTests.login
    post = test_api.ApiWorkflowTests.post
    completed = test_api.ApiWorkflowTests.completed

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "workflow.db"
        self.env = patch.dict(os.environ, {"RETAILPILOT_MODEL_PROVIDER": "rules"})
        self.env.start()
        self.client = TestClient(app, headers=HEADERS)
        self.client.__enter__()
        self.seed_fixture()
        self.login("admin")
        config = self.client.get("/api/model-config").json()
        self.saved = self.post(
            "/model-config",
            {
                "provider": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
                "model_name": "saved-model",
                "api_key": TEST_KEY,
                "max_output": 800,
                "daily_budget": 100000,
                "revision": config["revision"],
            },
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DB_PATH = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def test_task_uses_one_configuration_snapshot_and_never_persists_key(self):
        calls = []

        async def post(client, url, **kwargs):
            payload = kwargs["json"]
            calls.append(payload["model"])
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TEST_KEY)
            if payload["response_format"]["json_schema"]["name"] == "plan-review-v1":
                content = {"matches": True, "clarification": ""}
                # An administrator switches mode between the two model stages.
                from backend.model_config import save_config

                save_config(
                    {
                        "provider": "rules",
                        "base_url": self.saved["base_url"],
                        "model_name": "changed-model",
                        "api_key": "",
                        "clear_api_key": False,
                        "max_output": 800,
                        "daily_budget": 100000,
                        "revision": self.saved["revision"],
                    },
                    db.get("users", "admin"),
                )
            else:
                evidence = json.loads(payload["messages"][1]["content"])
                content = {"evidence_id": evidence["evidence_id"], "actions": ["monitor"]}
            return ModelConfigurationTests.response(
                url, content, {"total_tokens": 20, "secret_echo": TEST_KEY}
            )

        self.login("demo")
        with patch("httpx.AsyncClient.post", post):
            task = self.completed("2026 年 8 月华东订单数和客单价相比 7 月变化多少？")
        self.assertEqual(calls, ["saved-model", "saved-model"])
        self.assertEqual(task["result"]["metric_results"][0]["current"], 248)
        self.assertEqual(task["model"]["model"], "saved-model")
        self.assertEqual(task["writer_model"]["model"], "saved-model")
        self.assertNotIn(TEST_KEY, json.dumps(task, ensure_ascii=False))
        self.assertFalse(self.client.get("/api/model-status").json()["enabled"])
        next_task = self.completed("2026 年 8 月订单数")
        self.assertEqual(next_task["model"]["provider"], "rules")
