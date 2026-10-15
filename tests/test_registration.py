import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import storage as db
from backend.app import app
from backend.manage import init_admin

HEADERS = {"X-Requested-With": "RetailPilot"}
PASSWORD = "Member-password-123"


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.temporary.name) / "registration.db"
        init_admin("owner", "负责人", "Owner-password-123")
        self.client = TestClient(app, headers=HEADERS)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DB_PATH = self.previous
        self.temporary.cleanup()

    def register(self, **updates):
        return self.client.post(
            "/api/register",
            json={
                "username": "new.member-1",
                "name": "  新同事  ",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
                **updates,
            },
        )

    def test_register_login_and_administrator_authorization(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.text)
        account = response.json()
        self.assertEqual(account["name"], "新同事")
        self.assertEqual(account["roles"], ["store"])
        self.assertEqual(account["stores"], [])
        self.assertNotIn("password", account)
        self.assertNotIn("salt", account)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        saved = db.get("users", account["id"])
        self.assertNotEqual(saved["password"], PASSWORD)
        self.assertEqual(saved["password"], db.password_hash(PASSWORD, saved["salt"]))

        login = self.client.post(
            "/api/login", json={"username": account["id"], "password": PASSWORD}
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(self.client.get("/api/me").json()["id"], account["id"])
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        self.assertEqual(self.client.get("/api/model-config").status_code, 403)
        self.assertEqual(self.client.get("/api/versions").json(), [])
        self.assertEqual(self.client.get("/api/stores").json(), [])
        self.assertEqual(self.client.get("/api/dashboard").status_code, 404)
        self.assertEqual(self.client.post("/api/imports", json={}).status_code, 403)
        self.assertTrue(
            any(
                row["action"] == "注册账号"
                for row in self.client.get("/api/audit").json()
            )
        )

        self.client.post(
            "/api/login", json={"username": "owner", "password": "Owner-password-123"}
        )
        self.assertIn(
            account["id"], [row["id"] for row in self.client.get("/api/users").json()]
        )
        authorized = self.client.post(
            "/api/users/" + account["id"],
            json={"roles": ["analyst"], "stores": ["SH01"]},
        )
        self.assertEqual(authorized.status_code, 200, authorized.text)
        login = self.client.post(
            "/api/login", json={"username": account["id"], "password": PASSWORD}
        )
        self.assertEqual(login.json()["roles"], ["analyst"])
        self.assertEqual(login.json()["stores"], ["SH01"])

    def test_duplicate_does_not_replace_account_or_password(self):
        original = db.get("users", "owner")
        response = self.register(username="owner")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.get("users", "owner"), original)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)

    def test_concurrent_registration_only_creates_one_account(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.register().status_code, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(len(db.all_objects("users")), 2)

    def test_invalid_fields_and_privilege_injection_are_rejected(self):
        for updates in [
            {"username": "中文账号"},
            {"username": "ab"},
            {"name": "   "},
            {"password": "short", "confirm_password": "short"},
            {"confirm_password": "different-password"},
            {"roles": ["admin"]},
            {"stores": ["SH01"]},
            {"enabled": True},
        ]:
            with self.subTest(updates=updates):
                response = self.register(**updates)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(db.all_objects("users")), 1)

    def test_registration_keeps_request_origin_protection(self):
        payload = {
            "username": "new-member",
            "name": "同事",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        }
        for headers in [
            {"X-Requested-With": ""},
            {"Origin": "https://another.example"},
        ]:
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.client.post(
                        "/api/register", json=payload, headers=headers
                    ).status_code,
                    403,
                )
        self.assertEqual(len(db.all_objects("users")), 1)

    def test_rate_limit_is_persistent_expires_and_is_separate_from_login(self):
        with patch("backend.app.time.time", return_value=10000):
            self.assertEqual(self.register().status_code, 201)
            for _ in range(9):
                self.assertEqual(self.register().status_code, 409)
            self.assertEqual(self.register(username="another-member").status_code, 429)
            login = self.client.post(
                "/api/login",
                json={"username": "owner", "password": "Owner-password-123"},
            )
            self.assertEqual(login.status_code, 200)
        # Reopening the HTTP client must not reset the database-backed quota.
        with TestClient(app, headers=HEADERS) as another:
            with patch("backend.app.time.time", return_value=10001):
                self.assertEqual(
                    another.post(
                        "/api/register",
                        json={
                            "username": "another-member",
                            "name": "同事",
                            "password": PASSWORD,
                            "confirm_password": PASSWORD,
                        },
                    ).status_code,
                    429,
                )
        with patch("backend.app.time.time", return_value=10601):
            self.assertEqual(self.register(username="another-member").status_code, 201)

    def test_empty_workspace_still_requires_local_administrator_setup(self):
        with db.connection() as con:
            con.execute("DELETE FROM objects WHERE kind='users'")
        self.assertEqual(self.register().status_code, 503)
        self.assertEqual(db.all_objects("users"), [])
        init_admin("owner", "负责人", "Owner-password-123")
        self.assertEqual(self.register().status_code, 201)
