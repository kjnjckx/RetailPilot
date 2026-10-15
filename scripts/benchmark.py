"""Repeatable isolated 1k/10k/100k API benchmark; never opens the live database."""

import asyncio
import csv
import io
import json
import platform
import resource
import statistics
import tempfile
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend import storage as db
from backend.app import app
from backend.domain import FIELDS
from backend.query import cached

HEADERS = {"X-Requested-With": "RetailPilot"}


def dataset(size):
    data = {
        "stores": [
            {"store_id": f"S{i}", "name": f"测试门店{i}", "region": "华东"}
            for i in range(10)
        ],
        "products": [{"product_id": "P", "name": "测试商品", "category": "日用"}],
        "orders": [],
        "items": [],
    }
    for i in range(size):
        day = f"2026-{7 if i < size // 2 else 8:02}-{i % 28 + 1:02}"
        oid = f"O{i}"
        refund = "1.00" if i % 50 == 0 else "0.00"
        data["orders"].append(
            {
                "order_id": oid,
                "store_id": f"S{i % 10}",
                "paid_at": day + "T12:00:00+08:00",
                "status": "部分退款" if refund != "0.00" else "已支付",
                "paid_amount": "10.00",
                "refund_amount": refund,
            }
        )
        data["items"].append(
            {
                "order_id": oid,
                "line_id": "1",
                "product_id": "P",
                "quantity": "2",
                "returned_quantity": "0",
                "paid_amount": "10.00",
                "refund_amount": refund,
            }
        )
    files = {}
    for table, rows in data.items():
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=FIELDS[table])
        writer.writeheader()
        writer.writerows(rows)
        files[table] = out.getvalue()
    return files


def measure(client, url, cold=False, n=50):
    times = []
    sizes = []
    failures = 0
    for _ in range(n):
        if cold:
            cached.cache_clear()
        start = time.perf_counter()
        r = client.get(url)
        times.append((time.perf_counter() - start) * 1000)
        sizes.append(len(r.content))
        failures += r.status_code != 200
    ordered = sorted(times)
    return {
        "samples": n,
        "p50_ms": round(statistics.median(times), 3),
        "p95_ms": round(ordered[int(0.95 * n) - 1], 3),
        "max_ms": round(max(times), 3),
        "max_response_bytes": max(sizes),
        "failures": failures,
    }


def main():
    results = {
        "at": db.now(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
            "sqlite": __import__("sqlite3").sqlite_version,
        },
        "transport": "FastAPI TestClient in-process; not network or remote deployment latency",
        "sizes": [],
    }
    original = db.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as folder:
            for size in (1000, 10000, 100000):
                db.DB_PATH = Path(folder) / f"bench-{size}.db"
                cached.cache_clear()
                with TestClient(app, headers=HEADERS) as client:
                    salt = "00" * 16
                    for i in range(5):
                        db.put(
                            "users",
                            {
                                "id": f"user{i}",
                                "name": f"用户{i}",
                                "roles": ["analyst", "manager"],
                                "stores": [f"S{j}" for j in range(10)],
                                "salt": salt,
                                "password": db.password_hash(
                                    "Benchmark-password-123", salt
                                ),
                            },
                        )
                    login = client.post(
                        "/api/login",
                        json={
                            "username": "user0",
                            "password": "Benchmark-password-123",
                        },
                    )
                    assert login.status_code == 200
                    files = dataset(size)
                    versions = []
                    uploads = []
                    for i in range(5):
                        started = time.perf_counter()
                        r = client.post(
                            "/api/imports",
                            json={"files": files, "source": "独立生成的性能夹具"},
                        )
                        assert r.status_code == 200, r.text
                        batch = r.json()
                        assert batch["status"] == "validated", batch["issues"][:2]
                        r = client.post(
                            "/api/imports/" + batch["id"] + "/publish",
                            json={
                                "coverage_start": "2026-07-01",
                                "coverage_end": "2026-08-31",
                                "snapshot_at": f"2026-09-{i + 1:02}T00:00:00+08:00",
                                "accept_warnings": True,
                            },
                        )
                        assert r.status_code == 200, r.text
                        versions.append(r.json()["id"])
                        uploads.append((time.perf_counter() - started) * 1000)
                    url = (
                        "/api/dashboard?version="
                        + versions[-1]
                        + "&start=2026-08-01&end=2026-08-31"
                    )
                    cold = measure(client, url, True)
                    hot = measure(client, url)
                    history_scope = [f"S{i}" for i in range(10)]
                    history_result = {
                        "current": client.get(url).json()["current"],
                        "previous": client.get(url).json()["previous"],
                    }
                    with db.connection() as con:
                        for i in range(1000):
                            base = {
                                "id": f"H{i}",
                                "at": db.now(),
                                "scope": history_scope,
                                "version_id": versions[-1],
                                "result": history_result,
                                "creator": "性能夹具",
                                "user_id": "user0",
                            }
                            db.save(
                                con,
                                "tasks",
                                {
                                    **base,
                                    "status": "completed",
                                    "question": "性能历史记录",
                                    "steps": [],
                                },
                            )
                            db.save(
                                con,
                                "reports",
                                {
                                    **base,
                                    "status": "approved",
                                    "title": "性能历史报告",
                                    "revision": 1,
                                    "summary": "性能夹具，不作为业务结论",
                                    "reviews": [],
                                    "feedback": [],
                                },
                            )
                    endpoints = {
                        path: measure(client, path)
                        for path in [
                            "/api/versions",
                            "/api/tasks?page=1",
                            "/api/reports?page=1",
                            "/api/imports?page=1",
                        ]
                    }

                    async def shared_loop_reads():
                        async def reads(i):
                            async with httpx.AsyncClient(
                                transport=httpx.ASGITransport(app=app),
                                base_url="http://bench.test",
                                headers=HEADERS,
                                trust_env=False,
                            ) as other:
                                r = await other.post(
                                    "/api/login",
                                    json={
                                        "username": f"user{i}",
                                        "password": "Benchmark-password-123",
                                    },
                                )
                                assert r.status_code == 200
                                times = []
                                sizes = []
                                failures = 0
                                for _ in range(50):
                                    begin = time.perf_counter()
                                    r = await other.get(url)
                                    times.append((time.perf_counter() - begin) * 1000)
                                    sizes.append(len(r.content))
                                    failures += r.status_code != 200
                                return {
                                    "samples": 50,
                                    "p50_ms": round(statistics.median(times), 3),
                                    "p95_ms": round(sorted(times)[47], 3),
                                    "max_ms": round(max(times), 3),
                                    "max_response_bytes": max(sizes),
                                    "failures": failures,
                                }

                        async def import_again():
                            async with httpx.AsyncClient(
                                transport=httpx.ASGITransport(app=app),
                                base_url="http://bench.test",
                                headers=HEADERS,
                                trust_env=False,
                            ) as importer:
                                await importer.post(
                                    "/api/login",
                                    json={
                                        "username": "user0",
                                        "password": "Benchmark-password-123",
                                    },
                                )
                                begin = time.perf_counter()
                                r = await importer.post(
                                    "/api/imports",
                                    json={
                                        "files": files,
                                        "source": "与读取并行的导入夹具",
                                    },
                                )
                                assert r.status_code == 200, r.text
                                return {
                                    "duration_ms": round(
                                        (time.perf_counter() - begin) * 1000, 3
                                    ),
                                    "status": r.status_code,
                                }

                        return await asyncio.gather(
                            *[reads(i) for i in range(5)], import_again()
                        )

                    concurrent_results = asyncio.run(shared_loop_reads())
                    concurrent, parallel_import = (
                        concurrent_results[:5],
                        concurrent_results[5],
                    )
                    task = client.post(
                        "/api/tasks",
                        json={
                            "question": "2026 年 8 月订单数和客单价相比 7 月变化多少？",
                            "version_id": versions[-1],
                        },
                    ).json()
                    started = time.perf_counter()
                    while time.perf_counter() - started < 10:
                        detail = client.get("/api/tasks/" + task["id"]).json()
                        if detail["status"] not in ["queued", "running"]:
                            break
                        time.sleep(0.03)
                    assert detail["status"] == "completed", detail
                    row = {
                        "items_per_version": size,
                        "versions": 5,
                        "task_history": 1000,
                        "report_history": 1000,
                        "csv_bytes": sum(len(v.encode()) for v in files.values()),
                        "import_publish_ms": [round(x, 3) for x in uploads],
                        "cold_dashboard": cold,
                        "hot_dashboard": hot,
                        "endpoints": endpoints,
                        "five_concurrent_users": concurrent,
                        "concurrency_transport": "five AsyncClients on one ASGI event loop, with simultaneous import",
                        "parallel_import": parallel_import,
                        "analysis_stages": [
                            {"role": s["role"], "duration_ms": s["duration_ms"]}
                            for s in detail["steps"]
                        ],
                        "peak_rss_bytes": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss
                        * (1 if platform.system() == "Darwin" else 1024),
                        "database_bytes": db.DB_PATH.stat().st_size,
                    }
                    results["sizes"].append(row)
                    print(
                        json.dumps(
                            {
                                "size": size,
                                "cold_p95_ms": cold["p95_ms"],
                                "hot_p95_ms": hot["p95_ms"],
                                "upload_max_ms": max(uploads),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    finally:
        db.DB_PATH = original
        cached.cache_clear()
    output = Path("docs/benchmark-2026-10-02.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print("saved", output)


if __name__ == "__main__":
    main()
