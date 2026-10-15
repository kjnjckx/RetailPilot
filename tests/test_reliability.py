import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx

from backend import storage as db
from backend.domain import DomainError, aggregate, validate_import
from backend.manage import backup, restore
from backend.query import aggregate_version, cached
from tests import test_api
from tests.fixtures import demo_files, metric_data


class StorageReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "test.db"
        db.initialize()
        cached.cache_clear()

    def tearDown(self):
        db.DB_PATH = self.original
        self.tmp.cleanup()
        cached.cache_clear()

    def version(self):
        data = metric_data()
        v = {"id": "V", "data": data, "checksum": "test", "name": "fixture"}
        db.put("versions", v)
        return db.get("versions", "V", full=False)

    def test_sql_matches_independent_in_memory_aggregation_and_cache_scope(self):
        v = self.version()
        for scope in [["A", "B", "C"], ["A"], ["B"], ["C"]]:
            for start, end in [
                ("2026-07-01", "2026-07-31"),
                ("2026-08-01", "2026-08-31"),
            ]:
                self.assertEqual(
                    aggregate_version(v, scope, start, end),
                    aggregate(metric_data(), scope, start, end),
                )
        with self.assertRaises(DomainError):
            aggregate_version(v, ["A"], "2026-08-01", "2026-08-31", "B")

    def test_metadata_separation_and_legacy_migration(self):
        data = metric_data()
        with db.connection() as con:
            con.execute(
                "INSERT INTO objects VALUES(?,?,?)",
                ("versions", "old", json.dumps({"id": "old", "data": data})),
            )
        db.initialize()
        self.assertNotIn("data", db.get("versions", "old", full=False))
        self.assertEqual(db.get("versions", "old")["data"], data)
        with db.connection() as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_cross_process_claim_limit_and_cancel_compare_and_set(self):
        for i in range(6):
            db.put("tasks", {"id": str(i), "status": "queued", "steps": []})
        with ThreadPoolExecutor(5) as pool:
            claims = list(pool.map(db.claim_task, [str(i) for i in range(5)]))
        ids = [i for i in claims if i is not None]
        self.assertEqual(sorted(ids), ["0", "1"])
        db.initialize()  # a second worker starting cannot fail an active lease
        self.assertEqual(db.get("tasks", "0")["status"], "running")
        with db.connection() as con:
            owner = con.execute(
                "SELECT owner FROM task_leases WHERE id=?", ("0",)
            ).fetchone()[0]
        stale = db.get("tasks", "0")
        db.put("tasks", {"id": "0", "status": "cancelled", "steps": []})
        stale["status"] = "completed"
        self.assertFalse(db.update_task(stale, owner))
        self.assertEqual(db.get("tasks", "0")["status"], "cancelled")

    def test_independent_processes_claim_at_most_two_tasks(self):
        for i in range(6):
            db.put("tasks", {"id": str(i), "status": "queued", "steps": []})
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scripts.claim_worker",
                    "--database",
                    str(db.DB_PATH),
                    "--owner",
                    str(i),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(4)
        ]
        claimed = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err)
            value = json.loads(out)
            if value is not None:
                claimed.append(value)
        self.assertEqual(sorted(claimed), ["0", "1"])

    def test_expired_lease_and_queued_restart(self):
        db.put("tasks", {"id": "expired", "status": "running"})
        db.put("tasks", {"id": "waiting", "status": "queued"})
        with db.connection() as con:
            con.execute("INSERT INTO task_leases VALUES(?,?,0)", ("expired", "gone"))
        db.initialize()
        self.assertEqual(db.get("tasks", "expired")["status"], "failed")
        self.assertEqual(db.get("tasks", "waiting")["status"], "queued")

    def test_authorization_precedes_pagination(self):
        for i in range(80):
            db.put(
                "tasks",
                {
                    "id": str(i),
                    "scope": ["B"] if i % 2 else ["A"],
                    "status": "completed",
                    "result": {"large": "payload"},
                    "steps": [],
                },
            )
        result = db.summaries("tasks", ["A"], page=2, size=10)
        self.assertEqual(result["total"], 40)
        self.assertEqual(len(result["items"]), 10)
        self.assertTrue(
            all(r["scope"] == ["A"] and "result" not in r for r in result["items"])
        )

    def test_backup_restore_and_missing_confirmation(self):
        self.version()
        path = backup(Path(self.tmp.name) / "copy.db")
        db.put("users", {"id": "changed"})
        with self.assertRaises(ValueError):
            restore(path)
        restore(path, True)
        self.assertEqual(db.all_objects("users"), [])
        self.assertIsNotNone(db.get("versions", "V"))

    def test_business_fingerprint_row_order_and_real_changes(self):
        files = demo_files()
        _, _, original = validate_import(files)
        reordered = {
            t: "\n".join([v.splitlines()[0], *reversed(v.splitlines()[1:])]) + "\n"
            for t, v in files.items()
        }
        self.assertEqual(validate_import(reordered)[2], original)
        reordered["products"] = reordered["products"].replace("家居日用", "新的品类")
        self.assertNotEqual(validate_import(reordered)[2], original)

    def test_csv_bad_quote_oversize_encoding_and_ragged(self):
        for raw in [
            'store_id,name,region\nA,"open,华东',
            "store_id,name,region\nA," + "x" * 150000 + ",华东",
            "store_id,name,region\nA,\ufffd,华东",
            "store_id,name,region\nA,名字,华东,extra",
        ]:
            files = demo_files()
            files["stores"] = raw
            issues = validate_import(files)[1]
            self.assertTrue(
                any(i["table"] == "stores" and i["level"] == "error" for i in issues)
            )


class ExtendedApiTests(test_api.ApiWorkflowTests):
    # Only new methods are collected in this subclass; inherited cases run once in their original class.
    def test_cancel_during_query_does_not_start_statistics(self):
        from backend.query import aggregate_version

        entered = threading.Event()
        release = threading.Event()

        def blocked(*args, **kwargs):
            entered.set()
            release.wait(3)
            return aggregate_version(*args, **kwargs)

        with patch("backend.workflow.aggregate_version", blocked):
            task = self.post(
                "/tasks", {"question": "2026 年 8 月订单数相比 7 月变化多少？"}
            )
            self.assertTrue(entered.wait(3))
            self.post("/tasks/" + task["id"] + "/cancel")
            release.set()
            time.sleep(0.15)
        stored = db.get("tasks", task["id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertNotIn("统计分析", [s["role"] for s in stored["steps"]])

    def test_stage_failure_is_classified_and_cannot_create_report(self):
        with patch("backend.workflow.analyze", side_effect=ValueError("test failure")):
            task = self.post("/tasks", {"question": "2026 年 8 月订单数"})
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                stored = db.get("tasks", task["id"])
                if stored["status"] == "failed":
                    break
                time.sleep(0.03)
        self.assertEqual(stored["failed_stage"], "统计分析")
        self.assertEqual(stored["error_code"], "internal")
        self.post("/reports", {"task_id": task["id"]}, 400)

    def test_configured_model_roles_preserve_numeric_evidence(self):
        async def post(client, url, **kwargs):
            payload = kwargs["json"]
            if payload["response_format"]["json_schema"]["name"] == "plan-review-v1":
                content = {"matches": True, "clarification": ""}
            else:
                value = json.loads(payload["messages"][1]["content"])
                content = {"evidence_id": value["evidence_id"], "actions": ["monitor"]}
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(content)},
                        }
                    ],
                    "usage": {"total_tokens": 20},
                },
            )

        with (
            patch.dict(
                os.environ,
                {
                    "RETAILPILOT_MODEL_PROVIDER": "openai-compatible",
                    "RETAILPILOT_MODEL_API_KEY": "mock-only",
                    "RETAILPILOT_MODEL_NAME": "test-model",
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "ALL_PROXY": "",
                    "http_proxy": "",
                    "https_proxy": "",
                    "all_proxy": "",
                },
            ),
            patch("httpx.AsyncClient.post", post),
        ):
            task = self.completed("2026 年 8 月华东订单数和客单价相比 7 月变化多少？")
        self.assertEqual(task["model"]["model"], "test-model")
        self.assertEqual(
            task["writer_model"]["evidence_id"], task["result"]["evidence"]["id"]
        )
        self.assertEqual(task["result"]["metric_results"][0]["current"], 248)
        self.assertEqual(
            task["steps"][-1]["output"]["recommendation"],
            task["result"]["recommendation"],
        )
        self.assertEqual(sorted(task["scope"]), ["HZ01", "SH01"])
        self.assertTrue(
            all(
                s["duration_ms"] >= 0 and s["status"] == "completed"
                for s in task["steps"]
            )
        )

    def test_strict_types_unknown_fields_and_lengths(self):
        for body in [
            {"question": "2026 年 8 月订单数", "version_id": []},
            {"question": 3},
            {"question": "x" * 2001},
            {"question": "2026 年 8 月订单数", "injected": True},
        ]:
            self.post("/tasks", body, 400)
        self.assertEqual(self.client.get("/api/tasks?page=0").status_code, 422)

    def test_login_throttle_disable_reset_and_health(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        for _ in range(5):
            self.post("/login", {"username": "unknown", "password": "bad"}, 401)
        self.post("/login", {"username": "unknown", "password": "bad"}, 429)
        self.login("admin")
        self.post(
            "/users/store", {"roles": ["store"], "stores": ["SH01"], "enabled": False}
        )
        self.post("/login", {"username": "store", "password": "Retail2026!"}, 401)
        self.post(
            "/users/store", {"roles": ["store"], "stores": ["SH01"], "enabled": True}
        )
        self.post("/users/store/reset-password", {"password": "New-password-123"})
        self.post("/login", {"username": "store", "password": "Retail2026!"}, 401)
        self.post("/login", {"username": "store", "password": "New-password-123"})

    def test_same_business_snapshot_rejected_new_snapshot_allowed(self):
        b = self.post("/imports", {"files": demo_files()})
        body = {
            "coverage_start": "2026-07-01",
            "coverage_end": "2026-08-31",
            "snapshot_at": "2026-09-01T00:00:00+08:00",
            "accept_warnings": True,
        }
        self.post("/imports/" + b["id"] + "/publish", body, 400)
        body["snapshot_at"] = "2026-09-02T00:00:00+08:00"
        self.post("/imports/" + b["id"] + "/publish", body)

    def test_print_escape_and_structured_feedback(self):
        task = self.completed()
        r = self.post("/reports", {"task_id": task["id"]})
        base = "/reports/" + r["id"]
        self.post(base + "/edit", {"note": "<script>alert(1)</script>"})
        self.post(base + "/submit")
        self.post(base + "/approve", {"comment": "核验通过"})
        response = self.client.get("/api" + base + "/print")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert(1)", response.text)
        self.assertIn("&lt;script&gt;", response.text)
        updated = self.post(
            base + "/feedback",
            {
                "state": "已采纳",
                "suggestion": r["recommendation"],
                "owner": "负责人",
                "due": "2026-10-10",
                "action": "核对记录",
                "review_metric": "净销量变化",
                "comment": "待复核",
            },
        )
        self.assertEqual(updated["feedback"][0]["due"], "2026-10-10")


# Avoid repeating inherited tests (unittest discovers class methods across inheritance).
for _name in list(test_api.ApiWorkflowTests.__dict__):
    if _name.startswith("test_") and _name not in ExtendedApiTests.__dict__:
        setattr(ExtendedApiTests, _name, None)
