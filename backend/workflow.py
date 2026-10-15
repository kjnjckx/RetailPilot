"""Durable FIFO queue with SQLite leases and actual timed responsibilities."""

import asyncio
import logging
import secrets
import sqlite3
import time

from . import storage as db
from .domain import DomainError, analyze, plan_question
from .model_config import resolve_config
from .query import aggregate_version

log = logging.getLogger("retailpilot.workflow")


async def dispatcher(execute):
    owner = secrets.token_hex(16)
    workers = set()
    try:
        while True:
            try:
                id = await asyncio.to_thread(db.claim_task, owner)
            except sqlite3.OperationalError:
                log.error("queue_database_unavailable")
                await asyncio.sleep(1)
                continue
            if id:
                worker = asyncio.create_task(execute(id, owner))
                workers.add(worker)
                worker.add_done_callback(workers.discard)
            else:
                await asyncio.sleep(
                    0.15
                )  # queue polling, never decorative stage progress
    finally:
        for worker in list(workers):
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)


async def heartbeat(id, owner):
    while True:
        await asyncio.sleep(5)
        with db.connection() as con:
            con.execute(
                "UPDATE task_leases SET expires=? WHERE id=? AND owner=?",
                (time.time() + 30, id, owner),
            )


async def execute(id, owner, can_read, visible):
    beat = asyncio.create_task(heartbeat(id, owner))
    task = db.get("tasks", id)
    stage = "规划"
    step = None

    def authorize():
        current = db.get("tasks", id)
        if not current or current["status"] != "running":
            raise asyncio.CancelledError()
        u = db.get("users", task["user_id"])
        if (
            not u
            or not u.get("enabled", True)
            or not can_read(u)
            or not visible(u, task)
        ):
            raise DomainError("任务执行期间授权发生变化", 403)
        return u

    try:
        async with asyncio.timeout(120):
            # Credentials live only in this worker snapshot, never inside persisted tasks.
            model_config = resolve_config()
            task["engine"] = (
                "受限模型协作" if model_config.enabled else "规则分析 · 未连接大模型"
            )
            v = db.get("versions", task["version_id"], full=False)
            if not v:
                raise DomainError("原始数据版本不可用")
            query = None
            for stage in ["规划", "查询", "统计分析", "结果检查", "报告编制"]:
                u = authorize()
                started = time.perf_counter()
                step = {
                    "role": stage,
                    "status": "running",
                    "started_at": db.now(),
                    "input": {"version_id": v["id"], "scope": task["scope"]},
                    "output": {},
                }
                task["steps"].append(step)
                if not db.update_task(task, owner):
                    return
                if stage == "规划":
                    planned = plan_question(
                        task["question"],
                        {"stores": v["store_meta"], "products": v["product_meta"]},
                        u["stores"],
                        v["coverage_start"],
                        v["coverage_end"],
                    )
                    if planned != task["plan"]:
                        raise DomainError("已保存计划与当前权限不一致")
                    from .model_adapter import review_plan

                    task["model"] = await review_plan(task, v, config=model_config)
                    step["output"] = {
                        "plan": planned,
                        "engine": task["engine"],
                        "model": task["model"],
                    }
                elif stage == "查询":
                    p = task["plan"]
                    cur = await asyncio.to_thread(
                        aggregate_version,
                        v,
                        task["scope"],
                        p["start"],
                        p["end"],
                        p["store"],
                        p["region"],
                    )
                    authorize()  # Never start the baseline query after cancellation/revocation.
                    prev = (
                        await asyncio.to_thread(
                            aggregate_version,
                            v,
                            task["scope"],
                            p["previous_start"],
                            p["previous_end"],
                            p["store"],
                            p["region"],
                        )
                        if p["compare"]
                        else None
                    )
                    query = (cur, prev)
                    step["output"] = {
                        "current": cur,
                        "previous": prev,
                        "source": "versioned-sql",
                    }
                elif stage == "统计分析":
                    task["result"] = await asyncio.to_thread(
                        analyze,
                        None,
                        task["scope"],
                        task["plan"],
                        v["id"],
                        task["metric_version"],
                        query,
                    )
                    step["output"] = {
                        "metric_results": task["result"]["metric_results"],
                        "evidence_id": task["result"]["evidence"]["id"],
                    }
                    # The draft is withheld until independent verification passes.
                    draft = {
                        k: task["result"].pop(k)
                        for k in ("summary", "recommendation", "limits")
                    }
                elif stage == "结果检查":
                    result = task["result"]
                    cur = result["current"]
                    consistent = all(
                        sum(s[k] for s in cur["stores"]) == cur[k]
                        for k in ("net", "orders", "quantity")
                    )
                    evidence = result["evidence"]
                    consistent = (
                        consistent
                        and evidence["scope"] == [s["store_id"] for s in cur["stores"]]
                        and evidence["version_id"] == v["id"]
                    )
                    step["output"] = {
                        "evidence_present": bool(evidence),
                        "totals_consistent": consistent,
                    }
                    if not consistent:
                        raise DomainError("证据或合计校验失败")
                else:
                    from .model_adapter import compose_actions

                    actions = await compose_actions(task, config=model_config)
                    task["result"].update(draft)
                    if actions:
                        task["result"]["recommendation"] = actions["text"]
                    task["writer_model"] = actions
                    step["output"] = {
                        **{k: task["result"][k] for k in draft},
                        "evidence_id": task["result"]["evidence"]["id"],
                        "writer": actions,
                    }
                authorize()
                step.update(
                    status="completed",
                    ended_at=db.now(),
                    at=db.now(),
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                if stage == "报告编制":
                    task.update(status="completed", ended_at=db.now())
                if not db.update_task(task, owner):
                    return
            db.audit(u, "完成分析", id)
    except asyncio.CancelledError:
        # A cancelled task cannot be overwritten by a worker finishing later.
        task.update(
            status="failed",
            error="工作进程关闭导致任务中断，请重试。",
            ended_at=db.now(),
        )
        db.update_task(task, owner)
        raise
    except Exception as exc:
        if step:
            step.update(
                status="failed",
                ended_at=db.now(),
                error=exc.message if isinstance(exc, DomainError) else "阶段执行失败",
            )
        task.update(
            status="clarification"
            if isinstance(exc, DomainError) and exc.status == 422
            else "failed",
            error_code="timeout"
            if isinstance(exc, TimeoutError)
            else "permission"
            if isinstance(exc, DomainError) and exc.status == 403
            else "validation"
            if isinstance(exc, DomainError)
            else "internal",
            error=exc.message
            if isinstance(exc, DomainError)
            else "分析超时，请重试"
            if isinstance(exc, TimeoutError)
            else "分析处理失败，请重试",
            failed_stage=stage,
            ended_at=db.now(),
        )
        db.update_task(task, owner)
        log.error(
            "task_failed task_id=%s stage=%s error_type=%s",
            id,
            stage,
            type(exc).__name__,
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        with db.connection() as con:
            con.execute("DELETE FROM task_leases WHERE id=? AND owner=?", (id, owner))
