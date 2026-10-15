import asyncio
import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import httpx

from backend import storage as db
from backend.domain import DomainError
from backend.model_adapter import compose_actions, review_plan
from backend.workbook import convert
from tests.fixtures import demo_files


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "db"
        db.initialize()
        self.env = patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "all_proxy": "",
                "RETAILPILOT_MODEL_PROVIDER": "openai-compatible",
                "RETAILPILOT_MODEL_API_KEY": "test-only-key",
                "RETAILPILOT_MODEL_NAME": "mock-model",
                "RETAILPILOT_MODEL_DAILY_BUDGET": "100000",
            },
        )
        self.env.start()
        self.task = {
            "question": "2026 年 8 月订单数",
            "plan": {"metrics": ["orders"]},
            "scope": ["A"],
            "result": {
                "evidence": {
                    "id": "EV-TEST",
                    "metric_results": [
                        {
                            "metric": "orders",
                            "current": 3,
                            "previous": 2,
                            "delta": 1,
                            "rate": 50,
                        }
                    ],
                }
            },
        }

    def tearDown(self):
        self.env.stop()
        db.DB_PATH = self.old
        self.tmp.cleanup()

    def response(self, content, finish="stop"):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://mock.test"),
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": json.dumps(content)},
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    def test_review_and_writer_metadata_evidence_and_budget(self):
        async def post(client, url, **kwargs):
            self.assertTrue(kwargs["json"]["response_format"]["json_schema"]["strict"])
            content = (
                {"matches": True, "clarification": ""}
                if kwargs["json"]["response_format"]["json_schema"]["name"]
                == "plan-review-v1"
                else {"evidence_id": "EV-TEST", "actions": ["traffic", "monitor"]}
            )
            return self.response(content)

        with patch("httpx.AsyncClient.post", post):
            meta = asyncio.run(review_plan(self.task, {}))
            writer = asyncio.run(compose_actions(self.task))
        self.assertEqual(meta["model"], "mock-model")
        self.assertEqual(meta["usage"]["total_tokens"], 12)
        self.assertEqual(writer["evidence_id"], "EV-TEST")
        self.assertIn("复核", writer["text"])
        with db.connection() as con:
            self.assertGreater(
                con.execute("SELECT reserved FROM model_budget").fetchone()[0], 0
            )

    def test_bad_schema_injection_timeout_refusal_and_evidence_rejected(self):
        for content in [
            {"matches": True, "clarification": "", "stores": ["unauthorized"]},
            {"matches": "yes", "clarification": ""},
        ]:

            async def post(client, url, **kwargs):
                return self.response(content)

            with patch("httpx.AsyncClient.post", post), self.assertRaises(DomainError):
                asyncio.run(review_plan(self.task, {}))

        async def timeout(client, url, **kwargs):
            raise httpx.ReadTimeout("mock")

        with patch("httpx.AsyncClient.post", timeout), self.assertRaises(DomainError):
            asyncio.run(review_plan(self.task, {}))

        async def wrong(client, url, **kwargs):
            return self.response({"evidence_id": "other", "actions": ["invent-data"]})

        with patch("httpx.AsyncClient.post", wrong), self.assertRaises(DomainError):
            asyncio.run(compose_actions(self.task))

        async def incomplete(client, url, **kwargs):
            return self.response({"matches": True, "clarification": ""}, "length")

        with (
            patch("httpx.AsyncClient.post", incomplete),
            self.assertRaises(DomainError),
        ):
            asyncio.run(review_plan(self.task, {}))

    def test_disabled_missing_config_and_budget_are_explicit(self):
        with patch.dict(os.environ, {"RETAILPILOT_MODEL_PROVIDER": "rules"}):
            self.assertEqual(
                asyncio.run(review_plan(self.task, {}))["provider"], "rules"
            )
        for config in [
            {"RETAILPILOT_MODEL_API_KEY": ""},
            {"RETAILPILOT_MODEL_DAILY_BUDGET": "1"},
        ]:
            with patch.dict(os.environ, config), self.assertRaises(DomainError):
                asyncio.run(review_plan(self.task, {}))


class WorkbookTests(unittest.TestCase):
    def workbook(self, formula=False):
        out = io.BytesIO()
        files = demo_files()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr(
                "xl/workbook.xml",
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(
                    f'<sheet name="{t}" r:id="r{i}"/>' for i, t in enumerate(files)
                )
                + "</sheets></workbook>",
            )
            z.writestr(
                "xl/_rels/workbook.xml.rels",
                "<Relationships>"
                + "".join(
                    f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>'
                    for i, t in enumerate(files)
                )
                + "</Relationships>",
            )
            import csv

            for i, (table, text) in enumerate(files.items()):
                rows = []
                for ri, row in enumerate(csv.reader(io.StringIO(text)), 1):
                    rows.append(
                        f'<row r="{ri}">'
                        + "".join(
                            f'<c r="{chr(65 + ci)}{ri}" t="inlineStr">'
                            + ("<f>1+1</f>" if formula and ri == 2 else "")
                            + f"<is><t>{escape(v)}</t></is></c>"
                            for ci, v in enumerate(row)
                        )
                        + "</row>"
                    )
                z.writestr(
                    f"xl/worksheets/sheet{i}.xml",
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                    + "".join(rows)
                    + "</sheetData></worksheet>",
                )
        return base64.b64encode(out.getvalue()).decode()

    def test_valid_workbook_same_business_values(self):
        from backend.domain import validate_import

        result = convert(self.workbook())
        self.assertEqual(validate_import(result)[2], validate_import(demo_files())[2])

    def test_formula_corrupt_and_encoded_limit(self):
        for content in [
            self.workbook(True),
            "not-base64",
            base64.b64encode(b"not-a-zip").decode(),
        ]:
            with self.assertRaises(DomainError):
                convert(content)
