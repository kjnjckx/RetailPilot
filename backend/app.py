import asyncio
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import contracts as c
from . import storage as db
from . import workflow
from .auth import (
    can_read_data,
    object_for,
    public_user,
    require_data_role,
    role,
    user,
    visible,
)
from .domain import (
    FIELDS,
    METRIC_VERSION,
    DomainError,
    plan_question,
    scope_stores,
    validate_import,
)
from .model_adapter import configured, test_connection
from .model_config import draft_config, public_config, save_config
from .query import aggregate_version
from .reports import router as report_router


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    dispatcher = asyncio.create_task(workflow.dispatcher(execute_task))
    try:
        yield
    finally:
        dispatcher.cancel()
        await asyncio.gather(dispatcher, return_exceptions=True)


app = FastAPI(title="RetailPilot API", lifespan=lifespan)
app.include_router(report_router)


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@app.middleware("http")
async def protect(request, call_next):
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if request.headers.get("x-requested-with") != "RetailPilot" or (
            origin and urlparse(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"error": "请求来源验证失败"}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"error": "Content-Length 格式无效"}, status_code=400)
        if length > 28 * 1024 * 1024:
            return JSONResponse({"error": "上传内容超过 20 MB 限制"}, status_code=413)
        try:
            raw = await request.body()
            if len(raw) > 28 * 1024 * 1024:
                return JSONResponse({"error": "请求内容过大"}, status_code=413)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "请求体必须为 JSON 对象"}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def version_for(u, id=""):
    require_data_role(u)
    v = (
        db.get("versions", id, full=False)
        if id
        else next(
            (
                v
                for v in db.all_objects("versions", full=False)
                if any(s["store_id"] in u["stores"] for s in v["store_meta"])
            ),
            None,
        )
    )
    if v and "product_meta" not in v:
        v = db.get("versions", v["id"], full=False)
    if not v or not any(s["store_id"] in u["stores"] for s in v["store_meta"]):
        raise DomainError("没有可访问的已发布数据版本", 404)
    return v


def version_meta(v, u):
    allowed = u["stores"] if can_read_data(u) else []
    return {
        **{
            k: v[k]
            for k in [
                "id",
                "name",
                "at",
                "snapshot_at",
                "coverage_start",
                "coverage_end",
                "source",
                "creator",
            ]
        },
        "row_count": sum(
            n for store, n in v.get("store_rows", {}).items() if store in allowed
        ),
        "stores": scope_stores(
            {"stores": v.get("store_meta", v.get("data", {}).get("stores", []))},
            allowed,
        ),
    }


@app.post("/api/register", status_code=201)
async def register(request: Request):
    payload = await c.body(request, c.Register)
    name = payload["name"].strip()
    if not name:
        raise DomainError("姓名不可为空")
    if payload["password"] != payload["confirm_password"]:
        raise DomainError("两次密码不一致")

    now = time.time()
    key = "register:ip:" + (request.client.host if request.client else "unknown")
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        # Public registration must not prevent the initial administrator setup.
        if not con.execute(
            "SELECT 1 FROM objects WHERE kind='users' "
            "AND coalesce(json_extract(body,'$.enabled'),1)=1 "
            "AND EXISTS(SELECT 1 FROM json_each(body,'$.roles') WHERE value='admin') "
            "LIMIT 1"
        ).fetchone():
            raise DomainError("工作空间尚未开放注册，请联系负责人完成管理员初始化", 503)
        entry = con.execute(
            "SELECT failures,since FROM login_attempts WHERE key=?", (key,)
        ).fetchone()
        if entry and now - entry[1] < 600 and entry[0] >= 10:
            raise DomainError("注册尝试次数过多，请 10 分钟后重试", 429)
        attempts = entry[0] + 1 if entry and now - entry[1] < 600 else 1
        since = entry[1] if attempts > 1 else now
        con.execute(
            "INSERT INTO login_attempts VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,since=excluded.since",
            (key, attempts, since),
        )

    salt = secrets.token_hex(16)
    created = {
        "id": payload["username"],
        "name": name,
        "roles": ["store"],
        "stores": [],
        "enabled": True,
        "salt": salt,
        "password": await asyncio.to_thread(
            db.password_hash, payload["password"], salt
        ),
    }
    with db.connection() as con:
        try:
            con.execute(
                "INSERT INTO objects(kind,id,body) VALUES(?,?,?)",
                ("users", created["id"], json.dumps(created, ensure_ascii=False)),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError("账号已存在，请更换账号或直接登录", 409) from exc
    db.audit(created, "注册账号", created["id"])
    return public_user(created)


@app.post("/api/login")
async def login(request: Request):
    payload = await c.body(request, c.Login)
    now = time.time()
    keys = [
        "account:" + payload["username"],
        "ip:" + (request.client.host if request.client else "unknown"),
    ]
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        for key in keys:
            entry = con.execute(
                "SELECT failures,since FROM login_attempts WHERE key=?", (key,)
            ).fetchone()
            if (
                entry
                and now - entry[1] < 600
                and entry[0] >= (5 if key.startswith("account:") else 30)
            ):
                raise DomainError("登录失败次数过多，请 10 分钟后重试", 429)
            con.execute(
                "INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=CASE WHEN excluded.since-login_attempts.since>=600 THEN 1 ELSE login_attempts.failures+1 END,since=CASE WHEN excluded.since-login_attempts.since>=600 THEN excluded.since ELSE login_attempts.since END",
                (key, now),
            )
    u = db.get("users", str(payload.get("username", "")))
    if (
        not u
        or not u.get("enabled", True)
        or not hmac.compare_digest(
            await asyncio.to_thread(db.password_hash, payload["password"], u["salt"]),
            u["password"],
        )
    ):
        raise DomainError("账号或密码不正确", 401)
    with db.connection() as con:
        con.execute("DELETE FROM login_attempts WHERE key=?", (keys[0],))
        con.execute(
            "UPDATE login_attempts SET failures=max(0,failures-1) WHERE key=?",
            (keys[1],),
        )
    token = secrets.token_urlsafe(32)
    with db.connection() as con:
        con.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        con.execute(
            "INSERT INTO sessions VALUES(?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), u["id"], time.time() + 43200),
        )
    response = JSONResponse(public_user(u))
    response.set_cookie(
        "retailpilot_session",
        token,
        httponly=True,
        samesite="strict",
        max_age=43200,
        secure=request.url.scheme == "https",
    )
    db.audit(u, "登录系统", u["id"])
    return response


@app.post("/api/logout")
async def logout(request: Request):
    u = user(request)
    with db.connection() as con:
        con.execute(
            "DELETE FROM sessions WHERE token=?",
            (
                hashlib.sha256(
                    request.cookies.get("retailpilot_session", "").encode()
                ).hexdigest(),
            ),
        )
    response = JSONResponse({"ok": True})
    response.delete_cookie("retailpilot_session")
    db.audit(u, "退出登录", u["id"])
    return response


@app.get("/api/me")
async def me(request: Request):
    return public_user(user(request))


@app.post("/api/me/password")
async def change_password(request: Request):
    u = user(request)
    p = await c.body(request, c.Password)
    old = str(p.get("current_password", ""))
    new = str(p.get("new_password", ""))
    if not hmac.compare_digest(db.password_hash(old, u["salt"]), u["password"]):
        raise DomainError("当前密码不正确", 403)
    if len(new) < 12:
        raise DomainError("新密码至少 12 位")
    salt = secrets.token_hex(16)
    u.update(salt=salt, password=db.password_hash(new, salt))
    db.put("users", u)
    current_token = hashlib.sha256(
        request.cookies.get("retailpilot_session", "").encode()
    ).hexdigest()
    with db.connection() as con:
        con.execute(
            "DELETE FROM sessions WHERE user_id=? AND token<>?",
            (u["id"], current_token),
        )
    db.audit(u, "修改密码", u["id"])
    return {"ok": True}


@app.get("/api/versions")
async def versions(request: Request):
    u = user(request)
    if not can_read_data(u):
        return []
    return [
        version_meta(v, u)
        for v in db.all_objects("versions", full=False)
        if any(s["store_id"] in u["stores"] for s in v["store_meta"])
    ]


@app.get("/api/dashboard")
async def dashboard(
    request: Request,
    version: str = "",
    start: str = "",
    end: str = "",
    store: str = "",
):
    u = user(request)
    v = version_for(u, version)
    if not end:
        end = v["coverage_end"]
    if not start:
        start = max(v["coverage_start"], end[:7] + "-01")
    if start < v["coverage_start"] or end > v["coverage_end"]:
        raise DomainError("筛选日期超出数据版本覆盖范围")
    from datetime import date, timedelta

    cur = await asyncio.to_thread(aggregate_version, v, u["stores"], start, end, store)
    first = date.fromisoformat(start)
    span = (date.fromisoformat(end) - first).days + 1
    ps, pe = (
        (first - timedelta(days=span)).isoformat(),
        (first - timedelta(days=1)).isoformat(),
    )
    prev = (
        await asyncio.to_thread(aggregate_version, v, u["stores"], ps, pe, store)
        if ps >= v["coverage_start"]
        else None
    )
    return {
        "current": cur,
        "previous": prev,
        "previous_start": ps,
        "previous_end": pe,
        "version": version_meta(v, u),
        "metric_version": METRIC_VERSION,
    }


@app.get("/api/templates/{table}")
async def template(table: str, request: Request):
    user(request)
    if table not in FIELDS:
        raise DomainError("未知模板", 404)
    return Response(
        ",".join(FIELDS[table]) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


@app.post("/api/imports")
async def import_files(request: Request):
    u = user(request)
    role(u, "analyst", "admin")
    p = await c.body(request, c.Import)
    return await asyncio.to_thread(validate_batch, u, p)


def validate_batch(u, p):
    source_value = p.get("source")
    if not isinstance(source_value, str) or not source_value.strip():
        raise DomainError("请填写数据来源，例如业务系统及导出日期")
    source = source_value.strip()[:300]
    files = p.get("files", {})
    if (
        not isinstance(files, dict)
        or sum(len(str(x).encode()) for x in files.values()) > 20 * 1024 * 1024
    ):
        raise DomainError("单批 CSV 总量不能超过 20 MB", 413)
    mapping = p.get("mapping", {})
    if not isinstance(mapping, dict) or any(
        not isinstance(v, dict)
        or any(not isinstance(k, str) or not isinstance(x, str) for k, x in v.items())
        for v in mapping.values()
    ):
        raise DomainError("字段映射格式无效")
    data, issues, checksum = validate_import(files, mapping)
    if "admin" not in u["roles"] and any(
        s["store_id"] not in u["stores"] for s in data["stores"]
    ):
        raise DomainError("导入包含未授权门店，请先由管理员配置门店授权", 403)
    duplicate = next(
        (
            v["id"]
            for v in db.all_objects("versions", full=False)
            if v["checksum"] == checksum
        ),
        None,
    )
    batch = {
        "id": db.uid("IM"),
        "at": db.now(),
        "scope": [s["store_id"] for s in data["stores"]] or u["stores"],
        "name": str(p.get("name") or "经营数据导入")[:120],
        "source": source,
        "data": data,
        "issues": issues,
        "checksum": checksum,
        "raw_checksum": hashlib.sha256(
            json.dumps(files, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "duplicate": duplicate,
        "status": "invalid"
        if any(i["level"] == "error" for i in issues)
        else "validated",
        "creator": u["name"],
        "row_count": len(data["items"]),
        "coverage_start": min((o["date"] for o in data["orders"]), default=""),
        "coverage_end": max((o["date"] for o in data["orders"]), default=""),
    }
    fresh = db.get("users", u["id"])
    if not fresh or not fresh.get("enabled", True):
        raise DomainError("账号授权发生变化", 403)
    role(fresh, "analyst", "admin")
    if "admin" not in fresh["roles"] and not visible(fresh, batch):
        raise DomainError("导入期间门店授权发生变化", 403)
    db.put("imports", batch)
    db.audit(u, "校验导入数据", batch["id"])
    return {k: v for k, v in batch.items() if k != "data"}


@app.get("/api/imports")
async def imports(
    request: Request,
    page: int | None = Query(None, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=30),
):
    u = user(request)
    role(u, "analyst", "admin")
    result = await asyncio.to_thread(
        db.summaries,
        "imports",
        u["stores"],
        "admin" in u["roles"],
        False,
        page or 1,
        page_size,
        q,
        status,
    )
    return result if page is not None else result["items"]


@app.get("/api/imports/{id}/errors")
def errors(id: str, request: Request):
    u = user(request)
    role(u, "analyst", "admin")
    b = object_for(u, "imports", id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["table", "row", "level", "message"])
    for i in b["issues"]:
        writer.writerow(
            [
                i["table"],
                i["row"],
                i["level"],
                ("'" if str(i["message"]).startswith(("=", "+", "-", "@")) else "")
                + i["message"],
            ]
        )
    return Response(
        "\ufeff" + out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{id}-issues.csv"'},
    )


@app.post("/api/imports/{id}/publish")
async def publish(id: str, request: Request):
    u = user(request)
    role(u, "analyst", "admin")
    p = await c.body(request, c.Publish)
    return await asyncio.to_thread(publish_batch, u, id, p)


def publish_batch(u, id, p):
    b = object_for(u, "imports", id)
    if b["status"] != "validated":
        raise DomainError("仅可发布已通过校验且尚未发布的批次")
    if not b.get("source"):
        raise DomainError("该批次缺少数据来源，请重新上传并填写来源")
    if b["issues"] and not p.get("accept_warnings"):
        raise DomainError("请确认质量警告后再发布")
    try:
        start = datetime.fromisoformat(p["coverage_start"]).date().isoformat()
        end = datetime.fromisoformat(p["coverage_end"]).date().isoformat()
        snapshot = datetime.fromisoformat(p["snapshot_at"])
        if not snapshot.tzinfo:
            raise ValueError()
        snapshot = snapshot.astimezone(timezone(timedelta(hours=8)))
        if not snapshot.tzinfo or start > end or snapshot.date().isoformat() < end:
            raise ValueError()
        if any(
            not start <= o["date"] <= end
            or datetime.fromisoformat(o["paid_at"]) > snapshot
            for o in b["data"]["orders"]
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise DomainError("覆盖日期或快照时间无效，需覆盖所有订单且快照晚于成交时间")
    v = {
        "id": db.uid("V"),
        "at": db.now(),
        "name": b["name"],
        "creator": u["name"],
        "source": b["source"],
        "data": b["data"],
        "checksum": b["checksum"],
        "row_count": b["row_count"],
        "coverage_start": start,
        "coverage_end": end,
        "snapshot_at": snapshot.isoformat(),
    }
    b.update(status="published", version_id=v["id"])
    b.pop("data", None)
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        fresh = db.get("users", u["id"])
        if not fresh or not fresh.get("enabled", True):
            raise DomainError("账号授权发生变化", 403)
        role(fresh, "analyst", "admin")
        if "admin" not in fresh["roles"] and not visible(fresh, b):
            raise DomainError("发布期间门店授权发生变化", 403)
        existing = con.execute(
            "SELECT json_extract(body,'$.status') FROM objects WHERE kind='imports' AND id=?",
            (id,),
        ).fetchone()
        if not existing or existing[0] != "validated":
            raise DomainError("该批次已发布，请刷新")
        duplicate = con.execute(
            "SELECT id FROM objects WHERE kind='versions' AND json_extract(body,'$.checksum')=? AND json_extract(body,'$.coverage_start')=? AND json_extract(body,'$.coverage_end')=? AND json_extract(body,'$.snapshot_at')=?",
            (v["checksum"], start, end, snapshot.isoformat()),
        ).fetchone()
        if duplicate:
            raise DomainError("相同业务内容、覆盖范围和快照时间已发布，请勿重复发布")
        for kind, obj in [("versions", v), ("imports", b)]:
            db.save(con, kind, obj)
    v = db.get("versions", v["id"], full=False)
    db.audit(u, "发布数据版本", v["id"])
    return version_meta(v, u)


async def execute_task(id, owner):
    await workflow.execute(id, owner, can_read_data, visible)


@app.post("/api/tasks")
async def create_task(request: Request):
    u = user(request)
    role(u, "analyst", "manager", "store")
    p = await c.body(request, c.Task)
    v = version_for(u, p.get("version_id", ""))
    question = str(p.get("question", "")).strip()[:2000]
    if not question:
        raise DomainError("请输入经营问题")
    task = {
        "id": db.uid("TK"),
        "at": db.now(),
        "user_id": u["id"],
        "creator": u["name"],
        "question": question,
        "version_id": v["id"],
        "metric_version": METRIC_VERSION,
        "scope": [
            s["store_id"]
            for s in scope_stores(
                {"stores": v["store_meta"], "products": v["product_meta"]}, u["stores"]
            )
        ],
        "steps": [],
        "status": "queued",
        "engine": "受限模型协作" if configured() else "规则分析 · 未连接大模型",
    }
    if p.get("retry_of"):
        previous = object_for(u, "tasks", p["retry_of"])
        if previous["status"] not in ["cancelled", "failed", "clarification"]:
            raise DomainError("当前任务不需要重试")
        task["retry_of"] = previous["id"]
    try:
        task["plan"] = plan_question(
            question,
            {"stores": v["store_meta"], "products": v["product_meta"]},
            u["stores"],
            v["coverage_start"],
            v["coverage_end"],
        )
        task["scope"] = [
            s["store_id"]
            for s in scope_stores(
                {"stores": v["store_meta"], "products": v["product_meta"]},
                u["stores"],
                task["plan"]["store"],
                task["plan"]["region"],
            )
        ]
        if not task["scope"]:
            raise DomainError("所选地区没有可访问的门店", 422)
    except DomainError as exc:
        if exc.status != 422:
            raise
        task.update(status="clarification", error=exc.message)
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        active = con.execute(
            "SELECT count(*) FROM objects WHERE kind='tasks' AND json_extract(body,'$.user_id')=? AND json_extract(body,'$.status') IN ('queued','running')",
            (u["id"],),
        ).fetchone()[0]
        if task["status"] == "queued" and active >= 20:
            raise DomainError(
                "你已有 20 个排队或执行中的任务，请取消不需要的任务或稍后重试", 429
            )
        db.save(con, "tasks", task)
    db.audit(u, "发起分析", task["id"])
    return task


@app.get("/api/tasks")
async def tasks(
    request: Request,
    page: int | None = Query(None, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", max_length=200),
    status: str = Query("", max_length=30),
):
    u = user(request)
    require_data_role(u)
    result = await asyncio.to_thread(
        db.summaries,
        "tasks",
        u["stores"],
        False,
        False,
        page or 1,
        page_size,
        q,
        status,
    )
    return result if page is not None else result["items"]


@app.get("/api/tasks/{id}")
async def task_detail(id: str, request: Request):
    return object_for(user(request), "tasks", id)


@app.post("/api/tasks/{id}/cancel")
async def cancel(id: str, request: Request):
    u = user(request)
    t = object_for(u, "tasks", id)
    if t["user_id"] != u["id"]:
        raise DomainError("只能取消自己创建的任务", 403)
    if t["status"] not in ["queued", "running", "clarification"]:
        raise DomainError("任务已结束")
    t.update(status="cancelled", ended_at=db.now())
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute(
            "SELECT body FROM objects WHERE kind='tasks' AND id=?", (id,)
        ).fetchone()
        if not current or json.loads(current[0])["status"] not in [
            "queued",
            "running",
            "clarification",
        ]:
            raise DomainError("任务已结束")
        t = json.loads(current[0])
        t.update(status="cancelled", ended_at=db.now())
        db.save(con, "tasks", t)
        con.execute("DELETE FROM task_leases WHERE id=?", (id,))
    db.audit(u, "取消分析", id)
    return t


@app.get("/api/audit")
async def audit(request: Request):
    u = user(request)
    with db.connection() as con:
        rows = con.execute(
            "SELECT body FROM objects WHERE kind='audit'"
            + ("" if "admin" in u["roles"] else " AND json_extract(body,'$.user_id')=?")
            + " ORDER BY rowid DESC LIMIT 200",
            () if "admin" in u["roles"] else (u["id"],),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


@app.get("/api/users")
async def users(request: Request):
    role(user(request), "admin")
    return [public_user(u) for u in db.all_objects("users")]


@app.post("/api/users")
async def create_user(request: Request):
    actor = user(request)
    role(actor, "admin")
    p = await c.body(request, c.UserCreate)
    identifier = str(p.get("id", "")).strip()
    name = str(p.get("name", "")).strip()
    password = str(p.get("password", ""))
    roles = p.get("roles", [])
    stores = p.get("stores", [])
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", identifier):
        raise DomainError("账号需为 3–40 位字母、数字、下划线、点或连字符")
    if not name or len(name) > 80 or len(password) < 12:
        raise DomainError("姓名不可为空，密码至少 12 位")
    if (
        not isinstance(roles, list)
        or not roles
        or any(not isinstance(r, str) for r in roles)
        or not set(roles).issubset(["admin", "analyst", "manager", "store"])
        or not isinstance(stores, list)
        or any(not isinstance(s, str) or len(s) > 80 for s in stores)
    ):
        raise DomainError("角色或门店配置无效")
    salt = secrets.token_hex(16)
    created = {
        "id": identifier,
        "name": name,
        "roles": roles,
        "stores": stores,
        "salt": salt,
        "enabled": p["enabled"],
        "password": await asyncio.to_thread(db.password_hash, password, salt),
    }
    with db.connection() as con:
        try:
            con.execute(
                "INSERT INTO objects(kind,id,body) VALUES(?,?,?)",
                ("users", identifier, json.dumps(created, ensure_ascii=False)),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError("账号已存在", 409) from exc
    db.audit(actor, "创建账号", identifier)
    return public_user(created)


@app.post("/api/users/{id}")
async def update_user(id: str, request: Request):
    u = user(request)
    role(u, "admin")
    p = await c.body(request, c.UserUpdate)
    target = db.get("users", id)
    if not target:
        raise DomainError("用户不存在", 404)
    roles = p.get("roles", [])
    stores = p.get("stores", [])
    if (
        not isinstance(roles, list)
        or not isinstance(stores, list)
        or not roles
        or any(not isinstance(r, str) for r in roles)
        or not set(roles).issubset(["admin", "analyst", "manager", "store"])
        or any(not isinstance(s, str) or len(s) > 80 for s in stores)
    ):
        raise DomainError("角色或门店配置无效")
    if id == u["id"] and "admin" not in roles:
        raise DomainError("不能移除自己的管理员角色")
    if id == u["id"] and not p["enabled"]:
        raise DomainError("不能禁用自己的账号")
    target.update(roles=roles, stores=stores, enabled=p["enabled"])
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        db.save(con, "users", target)
        if not p["enabled"]:
            con.execute("DELETE FROM sessions WHERE user_id=?", (id,))
    db.audit(u, "更新角色与门店授权", id)
    return public_user(target)


@app.get("/api/stores")
async def store_catalog(request: Request):
    u = user(request)
    role(u, "admin", "analyst", "manager", "store")
    found = {}
    for v in db.all_objects("versions", full=False):
        for store in v["store_meta"]:
            if ("admin" in u["roles"] or store["store_id"] in u["stores"]) and store[
                "store_id"
            ] not in found:
                found[store["store_id"]] = store
    return sorted(found.values(), key=lambda s: s["store_id"])


@app.get("/api/imports/{id}")
async def import_detail(id: str, request: Request):
    u = user(request)
    role(u, "analyst", "admin")
    b = object_for(u, "imports", id)
    return {k: v for k, v in b.items() if k != "data"}


@app.post("/api/users/{id}/reset-password")
async def reset_password(id: str, request: Request):
    actor = user(request)
    role(actor, "admin")
    p = await c.body(request, c.Reset)
    target = db.get("users", id)
    if not target:
        raise DomainError("用户不存在", 404)
    salt = secrets.token_hex(16)
    target.update(
        salt=salt,
        password=await asyncio.to_thread(db.password_hash, p["password"], salt),
    )
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        db.save(con, "users", target)
        con.execute("DELETE FROM sessions WHERE user_id=?", (id,))
    db.audit(actor, "重置账号密码", id)
    return {"ok": True}


@app.get("/api/health")
async def health():
    try:
        with db.connection() as con:
            con.execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": 2}
    except sqlite3.Error:
        return JSONResponse({"status": "unavailable"}, status_code=503)


@app.get("/api/model-status")
async def model_status(request: Request):
    user(request)
    enabled = configured()
    return {
        "enabled": enabled,
        "engine": "受限模型协作" if enabled else "规则工作流 · 未连接大模型",
    }


@app.get("/api/model-config")
async def model_config(request: Request):
    actor = user(request)
    role(actor, "admin")
    return public_config()


@app.post("/api/model-config")
async def update_model_config(request: Request):
    actor = user(request)
    role(actor, "admin")
    payload = await c.body(request, c.ModelConfig)
    return save_config(payload, actor)


@app.post("/api/model-config/test")
async def test_model_config(request: Request):
    actor = user(request)
    role(actor, "admin")
    payload = await c.body(request, c.ModelConfig)
    try:
        config = draft_config(payload)
        result = await test_connection(config)
    except DomainError:
        db.audit(actor, "测试模型连接失败", "模型配置")
        raise
    db.audit(actor, "测试模型连接成功", "模型配置")
    return result


@app.post("/api/workbook/convert")
async def workbook_convert(request: Request):
    from .workbook import convert

    u = user(request)
    role(u, "analyst", "admin")
    p = await c.body(request, c.Workbook)
    return {"files": await asyncio.to_thread(convert, p["content"])}


if Path("dist").exists():
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            raise DomainError("接口不存在", 404)
        if path == "favicon.svg":
            return FileResponse("dist/favicon.svg")
        return FileResponse("dist/index.html")
