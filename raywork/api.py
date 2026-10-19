"""Single-workspace, version-aware content production API."""

import copy
import hashlib
import io
import json
import os
import secrets
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .generator import BLOCKS, fingerprint, generate, validate_blocks
from .store import Store, event, now, uid


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=100)
    audience: str = Field(default="", max_length=500)
    tone: str = Field(default="具体、真诚、有分寸", max_length=500)
    avoid: str = Field(default="不编造产品效果、价格和用户评价", max_length=1000)
    source_title: str = Field(default="客户提供的资料", min_length=1, max_length=100)
    source_text: str = Field(min_length=1, max_length=20000)


class SourceEdit(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    version: int = Field(ge=1)


class BlockEdit(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    version: int = Field(ge=1)


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class CommentInput(VersionInput):
    text: str = Field(min_length=1, max_length=2000)


class JobInput(BaseModel):
    key: str = Field(min_length=8, max_length=100, pattern=r"^[\w-]+$")
    target: Literal["angle", "title", "post", "cover", "script", "cta"] | None = None
    instruction: str = Field(default="", max_length=2000)
    version: int | None = Field(default=None, ge=1)


def require(condition, message, status=409):
    if not condition:
        raise HTTPException(status, message)


def block_at(project, key, version=None):
    block = project["blocks"].get(key)
    require(block is not None, "内容模块不存在", 404)
    if version is not None:
        require(block["version"] == version, "内容已经更新，请刷新后操作")
    return block


def checkpoint(block):
    block["history"].append(
        {
            k: copy.deepcopy(block[k])
            for k in ("text", "citations", "version", "status", "updated_at")
        }
    )


def create_app(data_dir, admin_token, mode="demo", generator=generate):
    if mode not in ("demo", "openai") or len(admin_token) < 24:
        raise ValueError("需要有效模式和至少 24 字符的管理令牌")
    store = Store(Path(data_dir) / "studio.sqlite3")
    app = FastAPI(
        title="RayWork Studio",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def bearer(authorization):
        return (
            authorization[7:]
            if authorization and authorization.startswith("Bearer ")
            else ""
        )

    def admin(authorization: str | None = Header(default=None)):
        require(
            secrets.compare_digest(
                bearer(authorization).encode(), admin_token.encode()
            ),
            "请先连接工作室",
            401,
        )

    def reviewer(authorization: str | None = Header(default=None)):
        digest = hashlib.sha256(bearer(authorization).encode()).hexdigest()
        with store.connection() as db:
            row = db.execute(
                "SELECT project_id,expires FROM shares WHERE hash=?", (digest,)
            ).fetchone()
        require(
            row is not None and row["expires"] > now(), "审稿链接已过期或被撤销", 401
        )
        return row["project_id"]

    def get_project(pid):
        project = store.get(pid)
        require(project is not None, "项目不存在", 404)
        return project

    def mutate(pid, fn):
        project = store.mutate(pid, fn)
        require(project is not None, "项目不存在", 404)
        return project

    def new_project(data):
        require(
            data.name.strip() and data.brand.strip() and data.source_text.strip(),
            "名称和资料不能全为空白",
            422,
        )
        project = {
            "id": uid(),
            "name": data.name.strip(),
            "brief": {
                k: getattr(data, k).strip()
                for k in ("brand", "audience", "tone", "avoid")
            },
            "sources": [
                {
                    "id": uid(),
                    "title": data.source_title,
                    "text": data.source_text.strip(),
                    "version": 1,
                }
            ],
            "blocks": {},
            "events": [],
            "created_at": now(),
            "updated_at": now(),
        }
        event(project, "created", "创建内容交付项目")
        with store.connection() as db:
            store.save(project, db)
        return project

    def apply_blocks(project, blocks, usage):
        for item in blocks:
            previous = project["blocks"].get(item["key"])
            if previous:
                checkpoint(previous)
            comments = previous["comments"] if previous else []
            project["blocks"][item["key"]] = {
                **item,
                "label": BLOCKS[item["key"]],
                "version": previous["version"] + 1 if previous else 1,
                "status": "changes"
                if any(not c["resolved"] for c in comments)
                else "draft",
                "stale": False,
                "comments": comments,
                "history": previous["history"] if previous else [],
                "updated_at": now(),
                "usage": usage,
                "approval": None,
            }

    def assert_job_allowed(project, data):
        if data.target:
            require(data.version is not None, "局部重写需要内容版本", 422)
            block = block_at(project, data.target, data.version)
            require(block["status"] != "approved", "已通过的内容需先撤回审核再修改")
        else:
            require(
                not project["blocks"], "已有内容请使用局部重写，避免覆盖已完成的工作"
            )

    def run_job(jid):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = store.job(jid, db)
            if not job or job["status"] != "queued":
                return
            job.update(status="running", started_at=now())
            store.save_job(job, db)
        try:
            blocks, usage = generator(
                job["snapshot"], job["target"], job["instruction"], mode
            )
            blocks = validate_blocks(blocks, job["snapshot"], job["target"])
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                project = store.get(job["project_id"], db)
                require(
                    fingerprint(project) == job["fingerprint"],
                    "生成期间资料已更新；请基于新资料重新生成",
                )
                assert_job_allowed(
                    project,
                    JobInput(
                        key=job["request_key"],
                        target=job["target"],
                        version=job["version"],
                    ),
                )
                apply_blocks(project, blocks, usage)
                event(
                    project,
                    "generated",
                    f"{'重写 ' + BLOCKS[job['target']] if job['target'] else '生成 6 个内容模块'} · {mode}",
                )
                store.save(project, db)
                job.update(
                    status="succeeded", usage=usage, error=None, finished_at=now()
                )
                store.save_job(job, db)
        except Exception as exc:
            # Provider/network exceptions can contain request details; expose only safe messages.
            error = (
                exc.detail
                if isinstance(exc, HTTPException)
                else str(exc)
                if isinstance(exc, ValueError)
                and not isinstance(exc, json.JSONDecodeError)
                else "生成失败，结果未覆盖；请检查连接和模型输出后重试"
            )
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                job.update(status="failed", error=error[:500], finished_at=now())
                store.save_job(job, db)

    def public_job(job):
        return {
            k: v
            for k, v in job.items()
            if k
            not in ("snapshot", "fingerprint", "key", "request_key", "request_hash")
        }

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": "0.1.0",
            "mode": mode,
            "configured": mode == "demo"
            or bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL")),
        }

    @app.get("/api/projects", dependencies=[Depends(admin)])
    def projects():
        return sorted(store.projects(), key=lambda p: p["updated_at"], reverse=True)

    @app.post("/api/projects", dependencies=[Depends(admin)], status_code=201)
    def create_project(data: ProjectInput):
        return new_project(data)

    @app.post("/api/demo", dependencies=[Depends(admin)], status_code=201)
    def demo():
        project = new_project(
            ProjectInput(
                name="秋日手冲 · 第 01 周内容包",
                brand="山间来信",
                audience="喜欢在家手冲、关注生活仪式感的城市青年",
                tone="克制、温暖、具体；不堆砌形容词",
                source_title="虚构品牌 · 秋日产品简报",
                source_text="山间来信秋日拼配咖啡豆，250g 袋装，中度烘焙。\n建议冲煮方式：手冲；推荐粉水比 1:15，可按口味调整。\n包装采用暖白底色和深绿色字标。\n本次内容主题：在忙碌的工作日，为自己留出十分钟手冲时间。\n本项目所有品牌、产品和营销场景均为虚构演示，不含真实商业承诺。",
            )
        )
        blocks, usage = generate(project, mode="demo")

        def seed(p):
            apply_blocks(p, blocks, usage)
            event(p, "demo", "加载本地模板示例；内容待人工审核")

        return mutate(project["id"], seed)

    @app.get("/api/projects/{pid}", dependencies=[Depends(admin)])
    def project(pid: str):
        return get_project(pid)

    @app.put("/api/projects/{pid}/sources/{sid}", dependencies=[Depends(admin)])
    def source(pid: str, sid: str, data: SourceEdit):
        def change(p):
            item = next((s for s in p["sources"] if s["id"] == sid), None)
            require(item is not None, "资料不存在", 404)
            require(item["version"] == data.version, "资料已更新，请刷新后操作")
            require(data.text.strip(), "资料不能为空", 422)
            if item["text"] == data.text.strip():
                return
            item.update(text=data.text.strip(), version=item["version"] + 1)
            for block in p["blocks"].values():
                if any(c["source_id"] == sid for c in block["citations"]):
                    block.update(stale=True, status="changes", approval=None)
            event(
                p,
                "source_updated",
                f"资料更新至 v{item['version']}，引用它的内容需重新生成和审核",
            )

        return mutate(pid, change)

    @app.put("/api/projects/{pid}/blocks/{key}", dependencies=[Depends(admin)])
    def edit_block(pid: str, key: str, data: BlockEdit):
        def change(p):
            b = block_at(p, key, data.version)
            require(b["status"] != "approved", "请先撤回审核再修改")
            require(data.text.strip(), "内容不能为空", 422)
            checkpoint(b)
            b.update(
                text=data.text.strip(),
                version=b["version"] + 1,
                status="changes"
                if b["stale"] or any(not c["resolved"] for c in b["comments"])
                else "draft",
                approval=None,
                updated_at=now(),
            )
            event(p, "edited", f"编辑 {b['label']} · v{b['version']}")

        return mutate(pid, change)

    def approve(pid, key, version, actor):
        def change(p):
            b = block_at(p, key, version)
            require(not b["stale"], "来源资料已变更，请先重写并核对内容")
            require(
                not any(not c["resolved"] for c in b["comments"]),
                "请先处理并标记解决所有批注",
            )
            b.update(
                status="approved",
                approval={"actor": actor, "at": now(), "version": version},
                updated_at=now(),
            )
            event(p, "approved", f"通过 {b['label']} · v{version}", actor)

        return mutate(pid, change)

    @app.post("/api/projects/{pid}/blocks/{key}/approve", dependencies=[Depends(admin)])
    def admin_approve(pid: str, key: str, data: VersionInput):
        return approve(pid, key, data.version, "工作室")

    @app.post("/api/projects/{pid}/blocks/{key}/reopen", dependencies=[Depends(admin)])
    def reopen(pid: str, key: str, data: VersionInput):
        def change(p):
            b = block_at(p, key, data.version)
            b.update(
                status="changes" if b["stale"] else "draft",
                approval=None,
                updated_at=now(),
            )
            event(p, "reopened", f"撤回 {b['label']} 的审核")

        return mutate(pid, change)

    def comment(pid, key, data, actor):
        def change(p):
            b = block_at(p, key, data.version)
            require(data.text.strip(), "批注不能为空", 422)
            b["comments"].append(
                {
                    "id": uid(),
                    "text": data.text.strip(),
                    "version": data.version,
                    "actor": actor,
                    "at": now(),
                    "resolved": False,
                }
            )
            b.update(status="changes", approval=None)
            event(p, "commented", f"批注 {b['label']} · v{data.version}", actor)

        return mutate(pid, change)

    @app.post(
        "/api/projects/{pid}/blocks/{key}/comments", dependencies=[Depends(admin)]
    )
    def admin_comment(pid: str, key: str, data: CommentInput):
        return comment(pid, key, data, "工作室")

    @app.post(
        "/api/projects/{pid}/blocks/{key}/comments/{cid}/resolve",
        dependencies=[Depends(admin)],
    )
    def resolve(pid: str, key: str, cid: str, data: VersionInput):
        def change(p):
            b = block_at(p, key, data.version)
            c = next((c for c in b["comments"] if c["id"] == cid), None)
            require(c is not None, "批注不存在", 404)
            c.update(resolved=True, resolved_at=now(), resolved_on_version=b["version"])
            event(p, "resolved", f"解决 {b['label']} 的一条批注")

        return mutate(pid, change)

    @app.post(
        "/api/projects/{pid}/jobs", dependencies=[Depends(admin)], status_code=202
    )
    def start_job(pid: str, data: JobInput, tasks: BackgroundTasks):
        request_hash = hashlib.sha256(
            data.model_dump_json(exclude={"key"}).encode()
        ).hexdigest()
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT data FROM jobs WHERE key=?", (pid + ":" + data.key,)
            ).fetchone()
            if old:
                job = json.loads(old[0])
                require(
                    job["request_hash"] == request_hash, "相同请求标识不能用于不同任务"
                )
                return public_job(job)
            p = store.get(pid, db)
            require(p is not None, "项目不存在", 404)
            assert_job_allowed(p, data)
            require(
                not any(
                    json.loads(r[0])["status"] in ("queued", "running")
                    for r in db.execute(
                        "SELECT data FROM jobs WHERE project_id=?", (pid,)
                    )
                ),
                "已有任务正在运行",
            )
            job = {
                "id": uid(),
                "project_id": pid,
                "key": pid + ":" + data.key,
                "request_key": data.key,
                "request_hash": request_hash,
                "target": data.target,
                "instruction": data.instruction,
                "version": data.version,
                "snapshot": p,
                "fingerprint": fingerprint(p),
                "status": "queued",
                "attempts": 1,
                "created_at": now(),
                "error": None,
                "usage": None,
            }
            store.save_job(job, db)
        tasks.add_task(run_job, job["id"])
        return public_job(job)

    @app.get("/api/projects/{pid}/jobs", dependencies=[Depends(admin)])
    def jobs(pid: str):
        get_project(pid)
        return [public_job(j) for j in store.jobs(pid)]

    @app.post("/api/jobs/{jid}/retry", dependencies=[Depends(admin)], status_code=202)
    def retry(jid: str, tasks: BackgroundTasks):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = store.job(jid, db)
            require(job is not None, "任务不存在", 404)
            require(job["status"] == "failed", "只有失败的任务可以重试")
            require(job["attempts"] < 3, "已重试三次，请检查配置后发起新任务")
            p = store.get(job["project_id"], db)
            assert_job_allowed(
                p,
                JobInput(
                    key=job["request_key"], target=job["target"], version=job["version"]
                ),
            )
            require(fingerprint(p) == job["fingerprint"], "资料已更新，请发起新任务")
            require(
                not any(
                    json.loads(r[0])["status"] in ("queued", "running")
                    for r in db.execute(
                        "SELECT data FROM jobs WHERE project_id=?", (p["id"],)
                    )
                ),
                "已有任务正在运行",
            )
            job.update(status="queued", attempts=job["attempts"] + 1, error=None)
            store.save_job(job, db)
        tasks.add_task(run_job, jid)
        return public_job(job)

    @app.post("/api/projects/{pid}/share", dependencies=[Depends(admin)])
    def share(pid: str):
        get_project(pid)
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(hours=72)).isoformat()
        with store.connection() as db:
            db.execute(
                "INSERT INTO shares VALUES(?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), pid, expires),
            )
        mutate(pid, lambda p: event(p, "shared", "创建 72 小时有效的客户审稿链接"))
        return {"token": token, "expires": expires}

    @app.delete("/api/projects/{pid}/share", dependencies=[Depends(admin)])
    def revoke(pid: str):
        get_project(pid)
        with store.connection() as db:
            db.execute("DELETE FROM shares WHERE project_id=?", (pid,))
        mutate(pid, lambda p: event(p, "revoked", "撤销所有客户审稿链接"))
        return {"ok": True}

    def review_view(p):
        # Reviewer gets current content and evidence, not historical drafts, costs or workspace events.
        return {k: copy.deepcopy(p[k]) for k in ("id", "name", "brief", "sources")} | {
            "blocks": {
                k: {
                    field: copy.deepcopy(b[field])
                    for field in (
                        "key",
                        "label",
                        "text",
                        "version",
                        "status",
                        "stale",
                        "citations",
                        "comments",
                        "approval",
                    )
                }
                for k, b in p["blocks"].items()
            }
        }

    @app.get("/api/review")
    def review(pid=Depends(reviewer)):
        return review_view(get_project(pid))

    @app.post("/api/review/blocks/{key}/comments")
    def review_comment(key: str, data: CommentInput, pid=Depends(reviewer)):
        return review_view(comment(pid, key, data, "客户审稿链接"))

    @app.post("/api/review/blocks/{key}/approve")
    def review_approve(key: str, data: VersionInput, pid=Depends(reviewer)):
        return review_view(approve(pid, key, data.version, "客户审稿链接"))

    @app.get("/api/projects/{pid}/export", dependencies=[Depends(admin)])
    def export(pid: str):
        p = get_project(pid)
        require(set(p["blocks"]) == set(BLOCKS), "需要完成全部 6 个内容模块")
        require(
            all(
                b["status"] == "approved"
                and not b["stale"]
                and not any(not c["resolved"] for c in b["comments"])
                for b in p["blocks"].values()
            ),
            "请先通过全部内容审核并处理批注，再导出交付包",
        )
        buffer = io.BytesIO()
        manifest = {"format": "raywork-delivery/1", "exported_at": now(), "project": p}
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                "README.txt",
                "RayWork Studio 内容交付包\n包含审核通过的文本、来源摘录、版本和审核记录。\n来源摘录匹配不等于事实核验；发布前仍需人工核对。\n若 usage.mode 为 demo，该模块来自本地模板，不是模型生成。\n",
            )
            for key in BLOCKS:
                b = p["blocks"][key]
                archive.writestr(
                    f"content/{key}.md", f"# {b['label']}\n\n{b['text']}\n"
                )

        def record_export(project):
            event(project, "exported", "导出已审核的 6 模块内容交付包")
            project["events"][-1]["versions"] = {
                key: b["version"] for key, b in p["blocks"].items()
            }

        mutate(pid, record_export)
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="raywork-delivery.zip"'
            },
        )

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static, check_dir=False), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
