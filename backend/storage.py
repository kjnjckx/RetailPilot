import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("RETAILPILOT_DB", ".data/retailpilot-live.db"))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "-" + secrets.token_hex(5).upper()


def password_hash(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def save(con, kind, obj):
    body = dict(obj)
    if kind == "imports":
        body["error_count"] = sum(i["level"] == "error" for i in body.get("issues", []))
        body["warning_count"] = sum(
            i["level"] == "warning" for i in body.get("issues", [])
        )
    if kind == "versions" and "data" in body:
        data = body.pop("data")
        body["store_meta"] = data["stores"]
        body["product_meta"] = data["products"]
        counts = {s["store_id"]: 0 for s in data["stores"]}
        orders = {o["order_id"]: o for o in data["orders"]}
        for item in data["items"]:
            counts[orders[item["order_id"]]["store_id"]] += 1
        body["store_rows"] = counts
        con.execute(
            "INSERT OR REPLACE INTO version_data VALUES(?,?)",
            (obj["id"], json.dumps(data, ensure_ascii=False)),
        )
        for table in ("orders", "items", "products"):
            con.execute("DELETE FROM v_" + table + " WHERE version=?", (obj["id"],))
        con.executemany(
            "INSERT INTO v_orders VALUES(?,?,?,?,?,?)",
            [
                (
                    obj["id"],
                    o["order_id"],
                    o["store_id"],
                    o["date"],
                    o["status"],
                    o["paid_amount"] - o["refund_amount"],
                )
                for o in data["orders"]
            ],
        )
        con.executemany(
            "INSERT INTO v_items VALUES(?,?,?,?,?,?)",
            [
                (
                    obj["id"],
                    i["order_id"],
                    i["line_id"],
                    i["product_id"],
                    i["paid_amount"] - i["refund_amount"],
                    i["quantity"] - i["returned_quantity"],
                )
                for i in data["items"]
            ],
        )
        con.executemany(
            "INSERT INTO v_products VALUES(?,?,?)",
            [(obj["id"], x["product_id"], x["category"]) for x in data["products"]],
        )
    con.execute(
        "INSERT INTO objects(kind,id,body) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
        (kind, obj["id"], json.dumps(body, ensure_ascii=False)),
    )


def put(kind, obj):
    with connection() as con:
        save(con, kind, obj)
    return obj


def get(kind, id, full=True):
    with connection() as con:
        row = con.execute(
            "SELECT body FROM objects WHERE kind=? AND id=?", (kind, id)
        ).fetchone()
    obj = json.loads(row[0]) if row else None
    if obj and kind == "versions" and full and "data" not in obj:
        with connection() as con:
            data = con.execute(
                "SELECT body FROM version_data WHERE id=?", (id,)
            ).fetchone()
        obj["data"] = (
            json.loads(data[0])
            if data
            else {"stores": [], "products": [], "orders": [], "items": []}
        )
    return obj


def all_objects(kind, full=True):
    with connection() as con:
        rows = con.execute(
            (
                "SELECT json_remove(body,'$.product_meta') FROM objects WHERE kind=? ORDER BY rowid DESC"
                if kind == "versions" and not full
                else "SELECT body FROM objects WHERE kind=? ORDER BY rowid DESC"
            ),
            (kind,),
        ).fetchall()
    objects = [json.loads(row[0]) for row in rows]
    if kind == "versions" and full:
        return [get(kind, obj["id"]) for obj in objects]
    return objects


def audit(user, action, target):
    put(
        "audit",
        {
            "id": uid("AU"),
            "at": now(),
            "actor": user["name"],
            "user_id": user["id"],
            "action": action,
            "target": target,
        },
    )


def initialize():
    with connection() as con:
        if con.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise ValueError("数据库版本比当前程序新，请使用匹配的程序版本")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS objects (kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(kind,id))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY,user_id TEXT NOT NULL, expires REAL NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS version_data (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS v_orders (version TEXT, id TEXT, store TEXT, date TEXT, status TEXT, net INTEGER, PRIMARY KEY(version,id))"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS orders_scope_date ON v_orders(version,store,date)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS v_items (version TEXT, order_id TEXT, line TEXT, product TEXT, net INTEGER, quantity INTEGER, PRIMARY KEY(version,order_id,line))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS v_products (version TEXT, id TEXT, category TEXT, PRIMARY KEY(version,id))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS task_leases (id TEXT PRIMARY KEY, owner TEXT, expires REAL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER, since REAL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS model_budget (day TEXT PRIMARY KEY, reserved INTEGER NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS model_config (id INTEGER PRIMARY KEY CHECK(id=1),"
            "provider TEXT NOT NULL,base_url TEXT NOT NULL,model_name TEXT NOT NULL,"
            "max_output INTEGER NOT NULL,daily_budget INTEGER NOT NULL,"
            "encrypted_api_key TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        con.execute("PRAGMA user_version=2")
        rows = con.execute("SELECT body FROM objects WHERE kind='versions'").fetchall()
        for row in rows:
            obj = json.loads(row[0])
            if "data" in obj:
                from .domain import business_checksum

                obj["checksum"] = business_checksum(obj["data"])
                save(con, "versions", obj)
        stale = con.execute(
            "SELECT o.body FROM objects o LEFT JOIN task_leases l ON o.id=l.id WHERE o.kind='tasks' AND json_extract(o.body,'$.status')='running' AND (l.id IS NULL OR l.expires<?)",
            (time.time(),),
        ).fetchall()
        for row in stale:
            task = json.loads(row[0])
            task.update(
                status="failed",
                error="执行租约中断或服务重启，请重试。",
                ended_at=now(),
            )
            save(con, "tasks", task)
        con.execute("DELETE FROM task_leases WHERE expires<?", (time.time(),))


def claim_task(owner, capacity=2):
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        # Recover expired workers without restarting this process.
        expired = con.execute(
            "SELECT id FROM task_leases WHERE expires<?", (time.time(),)
        ).fetchall()
        for row in expired:
            stored = con.execute(
                "SELECT body FROM objects WHERE kind='tasks' AND id=?", (row[0],)
            ).fetchone()
            if stored:
                task = json.loads(stored[0])
                if task["status"] == "running":
                    task.update(
                        status="failed", error="执行租约到期，请重试。", ended_at=now()
                    )
                    save(con, "tasks", task)
        con.execute("DELETE FROM task_leases WHERE expires<?", (time.time(),))
        if con.execute("SELECT count(*) FROM task_leases").fetchone()[0] >= capacity:
            return None
        row = con.execute(
            "SELECT body FROM objects WHERE kind='tasks' AND json_extract(body,'$.status')='queued' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return None
        task = json.loads(row[0])
        task.update(status="running", started_at=now())
        save(con, "tasks", task)
        con.execute(
            "INSERT INTO task_leases VALUES(?,?,?)",
            (task["id"], owner, time.time() + 30),
        )
        return task["id"]


def update_task(task, owner):
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT o.body FROM objects o JOIN task_leases l ON l.id=o.id WHERE o.kind='tasks' AND o.id=? AND l.owner=? AND l.expires>?",
            (task["id"], owner, time.time()),
        ).fetchone()
        if not row or json.loads(row[0])["status"] != "running":
            return False
        save(con, "tasks", task)
        if task["status"] != "running":
            con.execute(
                "DELETE FROM task_leases WHERE id=? AND owner=?", (task["id"], owner)
            )
        return True


def summaries(
    kind,
    allowed=None,
    admin=False,
    approved_only=False,
    page=1,
    size=30,
    q="",
    status="",
):
    conditions = ["kind=?"]
    params = [kind]
    if allowed is not None and not admin:
        conditions += [
            "json_array_length(body,'$.scope')>0",
            "NOT EXISTS (SELECT 1 FROM json_each(objects.body,'$.scope') s WHERE s.value NOT IN (SELECT value FROM json_each(?)))",
        ]
        params.append(json.dumps(allowed))
    if approved_only:
        conditions.append("json_extract(body,'$.status')='approved'")
    count_conditions = list(conditions)
    count_params = list(params)
    if status:
        conditions.append("json_extract(body,'$.status')=?")
        params.append(status)
    if q:
        conditions.append(
            "(instr(lower(coalesce(json_extract(body,'$.title'),json_extract(body,'$.question'),json_extract(body,'$.name'),'')), lower(?))>0 OR instr(id,?)>0)"
        )
        params += [q, q]
    where = " AND ".join(conditions)
    with connection() as con:
        counts = con.execute(
            "SELECT json_extract(body,'$.status') AS status,count(*) AS n FROM objects WHERE "
            + " AND ".join(count_conditions)
            + " GROUP BY status",
            count_params,
        ).fetchall()
        total = con.execute(
            "SELECT count(*) FROM objects WHERE " + where, params
        ).fetchone()[0]
        rows = con.execute(
            "SELECT json_remove(body,'$.data','$.result','$.steps','$.issues','$.reviews','$.feedback','$.note','$.recommendation') FROM objects WHERE "
            + where
            + " ORDER BY rowid DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        ).fetchall()
    return {
        "items": [json.loads(r[0]) for r in rows],
        "status_counts": {r["status"]: r["n"] for r in counts},
        "total": total,
        "page": page,
        "page_size": size,
        "pages": max(1, (total + size - 1) // size),
    }
