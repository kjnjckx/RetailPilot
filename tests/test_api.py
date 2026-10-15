import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import storage as db
from backend.app import app
from backend.domain import validate_import
from backend.manage import init_admin
from tests.fixtures import demo_files

HEADERS = {"X-Requested-With": "RetailPilot"}


class ApiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "test.db"
        self.client = TestClient(app, headers=HEADERS)
        self.client.__enter__()
        self.seed_fixture()
        self.login()

    def seed_fixture(self):
        # Historical workflow cases use an explicit test fixture; production starts empty.
        for username, name, roles, scope in [
            ("demo", "林晓", ["analyst", "manager"], ["SH01", "HZ01", "SZ01", "GZ01"]),
            ("store", "静安店员", ["store"], ["SH01"]),
            ("admin", "系统管理员", ["admin"], []),
        ]:
            salt = "00" * 16
            db.put(
                "users",
                {
                    "id": username,
                    "name": name,
                    "roles": roles,
                    "stores": scope,
                    "salt": salt,
                    "password": db.password_hash("Retail2026!", salt),
                },
            )
        data, issues, checksum = validate_import(demo_files())
        self.assertFalse([i for i in issues if i["level"] == "error"])
        db.put(
            "versions",
            {
                "id": "V2026-001",
                "name": "测试数据",
                "at": db.now(),
                "snapshot_at": "2026-09-01T00:00:00+08:00",
                "coverage_start": "2026-07-01",
                "coverage_end": "2026-08-31",
                "checksum": checksum,
                "data": data,
                "row_count": len(data["items"]),
                "source": "测试夹具",
                "creator": "测试",
            },
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DB_PATH = self.old_db
        self.tmp.cleanup()

    def login(self, name="demo"):
        response = self.client.post(
            "/api/login", json={"username": name, "password": "Retail2026!"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def post(self, path, body=None, status=200):
        if path == "/imports":
            body = {"source": "测试夹具", **(body or {})}
        r = self.client.post("/api" + path, json=body or {})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def completed(self, question="2026 年 8 月华东净销售额相比 7 月为什么下降？"):
        t = self.post("/tasks", {"question": question})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            t = self.client.get("/api/tasks/" + t["id"]).json()
            if t["status"] not in ("queued", "running"):
                break
            time.sleep(0.1)
        self.assertEqual(t["status"], "completed", t)
        return t

    def test_session_logout(self):
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.post("/logout")
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_scope_on_dashboard_and_tasks(self):
        self.login("store")
        r = self.client.get("/api/dashboard").json()
        self.assertEqual(r["current"]["net"], 4500000)
        self.assertEqual(len(r["version"]["stores"]), 1)
        self.assertEqual(self.client.get("/api/dashboard?store=HZ01").status_code, 403)
        self.post("/tasks", {"question": "2026 年 8 月杭州西湖店净销售额"}, 403)
        self.post("/imports", {"files": demo_files()}, 403)

    def test_admin_has_no_data_by_default(self):
        self.login("admin")
        self.assertEqual(self.client.get("/api/versions").json(), [])
        self.assertEqual(self.client.get("/api/dashboard").status_code, 403)
        self.assertEqual(len(self.client.get("/api/users").json()), 3)

    def test_csrf(self):
        r = self.client.post(
            "/api/tasks",
            json={"question": "2026 年 8 月销售额"},
            headers={"Origin": "https://other.example"},
        )
        self.assertEqual(r.status_code, 403)

    def test_validation_and_duplicate(self):
        batch = self.post("/imports", {"files": demo_files()})
        self.assertEqual(batch["status"], "validated")
        self.assertEqual(batch["duplicate"], "V2026-001")
        self.post("/imports/" + batch["id"] + "/publish", {}, 400)
        files = demo_files()
        files["items"] += files["items"].splitlines()[1] + "\n"
        bad = self.post("/imports", {"files": files})
        self.assertEqual(bad["status"], "invalid")
        self.post("/imports/" + bad["id"] + "/publish", {}, 400)

    def test_publish_and_old_report_stability(self):
        t = self.completed()
        r = self.post("/reports", {"task_id": t["id"]})
        files = demo_files()
        files["stores"] = files["stores"].replace("上海静安店", "上海静安新店")
        b = self.post("/imports", {"files": files})
        v = self.post(
            "/imports/" + b["id"] + "/publish",
            {
                "coverage_start": "2026-07-01",
                "coverage_end": "2026-08-31",
                "snapshot_at": "2026-09-01T00:00:00+08:00",
                "accept_warnings": True,
            },
        )
        self.assertNotEqual(v["id"], "V2026-001")
        old = self.client.get("/api/reports/" + r["id"]).json()
        self.assertEqual(old["version_id"], "V2026-001")
        self.assertEqual(old["result"]["current"]["stores"][0]["name"], "上海静安店")

    def test_report_review_export_feedback_and_revision(self):
        t = self.completed()
        self.assertEqual(len(t["steps"]), 5)
        self.assertTrue(t["steps"][3]["output"]["totals_consistent"])
        r = self.post("/reports", {"task_id": t["id"]})
        base = "/reports/" + r["id"]
        draft = self.client.get("/api" + base + "/export").text
        self.assertIn("草稿 · 未经审核", draft)
        self.post(base + "/approve", {"comment": "未经提交不能审核"}, 400)
        self.post(base + "/submit")
        self.post(base + "/reject", {"comment": "请补充业务背景"})
        self.post(base + "/edit", {"note": "背景已补充"})
        self.post(base + "/submit")
        r = self.post(base + "/approve", {"comment": "已核对证据和口径"})
        self.assertEqual(len(r["reviews"]), 2)
        self.post(base + "/edit", {"note": "覆盖审核正文"}, 400)
        export = self.client.get("/api" + base + "/export").text
        self.assertIn("正式报告 · 已审核", export)
        self.assertIn("80000.00", export)
        self.assertIn("100000.00", export)
        r = self.post(
            base + "/feedback",
            {"state": "已采纳", "owner": "门店负责人", "comment": "先核对客流"},
        )
        self.assertEqual(r["feedback"][0]["state"], "已采纳")
        revision = self.post(base + "/revise")
        self.assertNotEqual(revision["id"], r["id"])
        self.assertEqual(revision["revision"], 2)
        self.assertEqual(self.client.get("/api" + base).json()["status"], "approved")

    def test_report_visibility_and_revoked_scope(self):
        task = self.completed()
        r = self.post("/reports", {"task_id": task["id"]})
        self.login("store")
        self.assertEqual(self.client.get("/api/tasks/" + task["id"]).status_code, 404)
        self.assertEqual(
            self.client.get("/api/reports/" + r["id"] + "/export").status_code, 404
        )
        self.login("admin")
        self.post("/users/demo", {"roles": ["analyst", "manager"], "stores": ["SH01"]})
        self.login("demo")
        self.assertEqual(self.client.get("/api/reports/" + r["id"]).status_code, 404)
        self.assertEqual(
            self.client.get("/api/reports/" + r["id"] + "/export").status_code, 404
        )

    def test_cancellation_prevents_further_steps(self):
        task = self.post("/tasks", {"question": "2026 年 8 月净销售额"})
        self.post("/tasks/" + task["id"] + "/cancel")
        time.sleep(0.7)
        task = self.client.get("/api/tasks/" + task["id"]).json()
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["steps"], [])
        self.post("/reports", {"task_id": task["id"]}, 400)

    def test_clarification_and_retry(self):
        t = self.post("/tasks", {"question": "最近卖得如何"})
        self.assertEqual(t["status"], "clarification")
        self.assertEqual(t["steps"], [])
        retry = self.post(
            "/tasks", {"question": "2026 年 8 月净销售额", "retry_of": t["id"]}
        )
        self.assertEqual(retry["retry_of"], t["id"])
        self.post("/tasks/" + retry["id"] + "/cancel")

    def test_failure_recovery(self):
        db.put("tasks", {"id": "TK-INTERRUPTED", "status": "running"})
        db.initialize()
        t = db.get("tasks", "TK-INTERRUPTED")
        self.assertEqual(t["status"], "failed")

    def test_malformed_request_returns_validation_error(self):
        response = self.client.post("/api/tasks", content="not json", headers=HEADERS)
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/tasks", json=["array"], headers=HEADERS)
        self.assertEqual(response.status_code, 400)
        self.post("/imports", {"files": demo_files(), "mapping": "invalid"}, 400)

    def test_metric_comparison_survives_report_review_and_export(self):
        task = self.completed("2026 年 8 月有效订单数和净销量相比 7 月变化多少？")
        self.assertEqual(
            [m["metric"] for m in task["result"]["metric_results"]],
            ["orders", "quantity"],
        )
        self.assertEqual(task["result"]["delta"], 0)
        self.assertNotIn("元", task["result"]["summary"])
        report = self.post("/reports", {"task_id": task["id"]})
        base = "/reports/" + report["id"]
        self.post(base + "/submit")
        self.post(base + "/approve", {"comment": "核对订单数及销量"})
        exported = self.client.get("/api" + base + "/export")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("|有效订单数|496 笔|496 笔|0 笔|+0.00%|", exported.text)
        self.assertIn("净销量", exported.text)
        self.assertNotIn("净销售额：", exported.text)
        self.assertNotIn("人民币元", exported.text)
        self.assertEqual(report["metric_version"], task["metric_version"])

    def test_yoy_and_multiple_regions_require_clarification(self):
        for question, expected in [
            ("2026 年 8 月净销售额同比变化多少？", "2025-08-01"),
            ("2026 年 8 月华东和华南净销售额", "多地区"),
        ]:
            with self.subTest(question=question):
                task = self.post("/tasks", {"question": question})
                self.assertEqual(task["status"], "clarification")
                self.assertEqual(task["steps"], [])
                self.assertIn(expected, task["error"])
                self.assertNotIn("result", task)

    def test_data_read_role_matrix_including_detail_and_export(self):
        task = self.completed("2026 年 8 月上海静安店订单数")
        report = self.post("/reports", {"task_id": task["id"]})
        base = "/reports/" + report["id"]
        self.post(base + "/submit")
        self.post(base + "/approve", {"comment": "通过"})
        for roles in [
            ["admin"],
            ["analyst"],
            ["manager"],
            ["store"],
            ["admin", "analyst"],
        ]:
            with self.subTest(roles=roles):
                target = db.get("users", "store")
                target.update(roles=roles, stores=["SH01"])
                db.put("users", target)
                self.login("store")
                allowed = roles != ["admin"]
                versions = self.client.get("/api/versions")
                self.assertEqual(versions.status_code, 200)
                self.assertEqual(len(versions.json()), 1 if allowed else 0)
                for endpoint in ["/dashboard", "/tasks", "/reports"]:
                    self.assertEqual(
                        self.client.get("/api" + endpoint).status_code,
                        200 if allowed else 403,
                    )
                for endpoint in ["/tasks/" + task["id"], base, base + "/export"]:
                    self.assertEqual(
                        self.client.get("/api" + endpoint).status_code,
                        200 if allowed else 404,
                    )
        target.update(roles=["analyst"], stores=[])
        db.put("users", target)
        self.assertEqual(self.client.get("/api/versions").json(), [])
        self.assertEqual(self.client.get("/api/dashboard").status_code, 404)
        self.assertEqual(self.client.get("/api/tasks").json(), [])
        self.assertEqual(self.client.get("/api" + base + "/export").status_code, 404)

    def test_revoking_business_role_stops_running_task_and_hides_history(self):
        task = self.post("/tasks", {"question": "2026 年 8 月订单数"})
        self.login("admin")
        self.post(
            "/users/demo",
            {"roles": ["admin"], "stores": ["SH01", "HZ01", "SZ01", "GZ01"]},
        )
        time.sleep(0.7)
        stored = db.get("tasks", task["id"])
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["steps"], [])
        self.login("demo")
        self.assertEqual(self.client.get("/api/tasks").status_code, 403)
        self.assertEqual(self.client.get("/api/tasks/" + task["id"]).status_code, 404)
        self.post("/tasks", {"question": "2026 年 8 月订单数"}, 403)

    def test_historical_metric_version_is_used_after_engine_upgrade(self):
        task = self.completed()
        with (
            patch("backend.app.METRIC_VERSION", "sales-v2"),
            patch("backend.domain.METRIC_VERSION", "sales-v2"),
        ):
            report = self.post("/reports", {"task_id": task["id"]})
            base = "/reports/" + report["id"]
            self.assertEqual(report["metric_version"], "sales-v1")
            self.post(base + "/submit")
            self.post(base + "/approve", {"comment": "旧口径核验通过"})
            export = self.client.get("/api" + base + "/export").text
            self.assertIn("指标版本：sales-v1", export)
            self.assertNotIn("sales-v2", export)
            revision = self.post(base + "/revise")
            self.assertEqual(revision["metric_version"], "sales-v1")
            # Reports created before the new report field still use their saved evidence version.
            legacy = db.get("reports", report["id"])
            legacy.pop("metric_version")
            legacy["result"].pop("metric_version")
            legacy["result"].pop("metric_results")
            legacy["result"]["evidence"].pop("metric_results")
            db.put("reports", legacy)
            exported = self.client.get("/api" + base + "/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertIn("指标版本：sales-v1", exported.text)
            self.assertIn("80000.00", exported.text)

    def test_inconsistent_evidence_blocks_submission_and_export(self):
        task = self.completed()
        original = self.post("/reports", {"task_id": task["id"]})
        base = "/reports/" + original["id"]
        for change in ["metric_version", "version_id", "scope", "amount", "comparison"]:
            with self.subTest(change=change):
                db.put("reports", original)
                tampered = db.get("reports", original["id"])
                if change == "metric_version":
                    tampered["metric_version"] = "sales-v999"
                elif change == "version_id":
                    tampered["result"]["evidence"]["version_id"] = "another-version"
                elif change == "scope":
                    tampered["result"]["evidence"]["scope"] = ["SH01"]
                elif change == "amount":
                    tampered["result"]["current"]["net"] += 1
                else:
                    tampered["result"]["metric_results"][0]["delta"] += 1
                db.put("reports", tampered)
                self.post(base + "/submit", {}, 400)
                self.assertEqual(
                    self.client.get("/api" + base + "/export").status_code, 400
                )

    def test_missing_data_version_blocks_formal_export(self):
        task = self.completed()
        report = self.post("/reports", {"task_id": task["id"]})
        base = "/reports/" + report["id"]
        self.post(base + "/submit")
        self.post(base + "/approve", {"comment": "通过"})
        with db.connection() as con:
            con.execute(
                "DELETE FROM objects WHERE kind=? AND id=?",
                ("versions", report["version_id"]),
            )
        exported = self.client.get("/api" + base + "/export")
        self.assertEqual(exported.status_code, 400)
        self.assertIn("原始数据版本不可用", exported.json()["error"])


class FreshInstallTests(unittest.TestCase):
    def test_empty_start_admin_setup_and_csv_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = db.DB_PATH
            db.DB_PATH = Path(directory) / "live.db"
            try:
                with TestClient(app, headers=HEADERS) as client:
                    self.assertEqual(db.all_objects("users"), [])
                    self.assertEqual(db.all_objects("versions"), [])
                    self.assertEqual(client.get("/api/demo-files").status_code, 404)
                    init_admin("owner", "负责人", "A-strong-password-123")
                    with self.assertRaises(ValueError):
                        init_admin("second", "另一管理员", "A-strong-password-123")
                    response = client.post(
                        "/api/login",
                        json={
                            "username": "owner",
                            "password": "A-strong-password-123",
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    changed = client.post(
                        "/api/me/password",
                        json={
                            "current_password": "A-strong-password-123",
                            "new_password": "A-new-strong-password-456",
                        },
                    )
                    self.assertEqual(changed.status_code, 200, changed.text)
                    self.assertEqual(
                        client.post(
                            "/api/login",
                            json={
                                "username": "owner",
                                "password": "A-strong-password-123",
                            },
                        ).status_code,
                        401,
                    )
                    self.assertEqual(client.get("/api/me").status_code, 200)
                    self.assertEqual(client.get("/api/versions").json(), [])
                    missing = client.post("/api/imports", json={"files": demo_files()})
                    self.assertEqual(missing.status_code, 400)
                    created = client.post(
                        "/api/users",
                        json={
                            "id": "analyst1",
                            "name": "分析人员",
                            "password": "Another-password-123",
                            "roles": ["analyst"],
                            "stores": ["SH01", "HZ01", "SZ01", "GZ01"],
                        },
                    )
                    self.assertEqual(created.status_code, 200, created.text)
                    self.assertNotIn("password", created.json())
                    batch = client.post(
                        "/api/imports",
                        json={
                            "files": demo_files(),
                            "source": "POS 导出文件，已获许可",
                        },
                    )
                    self.assertEqual(batch.status_code, 200, batch.text)
                    self.assertEqual(batch.json()["coverage_start"], "2026-07-01")
                    self.assertEqual(len(client.get("/api/imports").json()), 1)
                    published = client.post(
                        "/api/imports/" + batch.json()["id"] + "/publish",
                        json={
                            "coverage_start": "2026-07-01",
                            "coverage_end": "2026-08-31",
                            "snapshot_at": "2026-09-01T00:00:00+08:00",
                            "accept_warnings": True,
                        },
                    )
                    self.assertEqual(published.status_code, 200, published.text)
                    self.assertEqual(client.get("/api/versions").json(), [])
                    client.post(
                        "/api/login",
                        json={
                            "username": "analyst1",
                            "password": "Another-password-123",
                        },
                    )
                    versions = client.get("/api/versions").json()
                    self.assertEqual(len(versions), 1)
                    self.assertEqual(versions[0]["source"], "POS 导出文件，已获许可")
                    self.assertEqual(
                        client.get(
                            "/api/dashboard?start=2026-08-01&end=2026-08-31"
                        ).status_code,
                        200,
                    )
            finally:
                db.DB_PATH = previous
