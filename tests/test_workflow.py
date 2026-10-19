import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from raywork.api import create_app
from raywork.generator import BLOCKS, generate
from raywork.store import Store, uid

TOKEN = "test-only-admin-token-at-least-24-chars"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path, TOKEN)
    with TestClient(app, headers=AUTH) as client:
        project = client.post("/api/demo").json()
        yield app, client, project


def route(p, suffix=""):
    return f"/api/projects/{p['id']}" + suffix


def test_full_client_feedback_revision_and_delivery(setup):
    app, client, p = setup
    assert client.get(route(p, "/export")).status_code == 409
    share = client.post(route(p, "/share")).json()
    reviewer = {"Authorization": "Bearer " + share["token"]}
    r = client.post(
        "/api/review/blocks/title/comments",
        headers=reviewer,
        json={"version": 1, "text": "请强调十分钟手冲"},
    )
    assert r.status_code == 200
    assert "history" not in r.json()["blocks"]["title"]
    assert "events" not in r.json()
    assert (
        client.post(
            "/api/review/blocks/title/approve", headers=reviewer, json={"version": 1}
        ).status_code
        == 409
    )
    before = client.get(route(p)).json()
    request = {
        "key": "revision-001",
        "target": "title",
        "version": 1,
        "instruction": "强调十分钟",
    }
    job = client.post(route(p, "/jobs"), json=request).json()
    after = client.get(route(p)).json()
    assert after["blocks"]["post"] == before["blocks"]["post"]
    assert after["blocks"]["title"]["version"] == 2
    assert (
        after["blocks"]["title"]["history"][0]["text"]
        == before["blocks"]["title"]["text"]
    )
    # Idempotent retry of the HTTP request cannot trigger another model call/version.
    assert client.post(route(p, "/jobs"), json=request).json()["id"] == job["id"]
    assert client.get(route(p)).json()["blocks"]["title"]["version"] == 2
    cid = after["blocks"]["title"]["comments"][0]["id"]
    assert (
        client.post(
            route(p, f"/blocks/title/comments/{cid}/resolve"), json={"version": 2}
        ).status_code
        == 200
    )
    for key in BLOCKS:
        version = after["blocks"][key]["version"]
        assert (
            client.post(
                f"/api/review/blocks/{key}/approve",
                headers=reviewer,
                json={"version": version},
            ).status_code
            == 200
        )
    result = client.get(route(p, "/export"))
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(archive.namelist()) == 8
        title = manifest["project"]["blocks"]["title"]
        assert title["approval"]["version"] == 2
        assert title["text"] in archive.read("content/title.md").decode()
        assert TOKEN.encode() not in archive.read("manifest.json")
        assert share["token"].encode() not in archive.read("manifest.json")


def test_admin_and_scoped_reviewer_auth(setup):
    app, client, p = setup
    share = client.post(route(p, "/share")).json()
    auth = {"Authorization": "Bearer " + share["token"]}
    other = client.post("/api/demo").json()
    assert client.get("/api/projects", headers={"Authorization": ""}).status_code == 401
    assert client.get(route(other), headers=auth).status_code == 401
    assert client.get("/api/review", headers=auth).json()["id"] == p["id"]
    assert (
        client.post(
            route(other, "/blocks/title/approve"), headers=auth, json={"version": 1}
        ).status_code
        == 401
    )
    assert client.get("/static/../.state/admin-token").status_code == 404
    client.delete(route(p, "/share"))
    assert client.get("/api/review", headers=auth).status_code == 401


def test_expired_review_link(setup):
    app, client, p = setup
    token = client.post(route(p, "/share")).json()["token"]
    with app.state.store.connection() as db:
        db.execute("UPDATE shares SET expires='2000-01-01T00:00:00+00:00'")
    assert (
        client.get(
            "/api/review", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_old_version_cannot_edit_or_approve(setup):
    app, client, p = setup
    response = client.put(
        route(p, "/blocks/title"), json={"version": 1, "text": "给自己十分钟，慢慢手冲"}
    )
    assert response.status_code == 200
    assert (
        client.post(route(p, "/blocks/title/approve"), json={"version": 1}).status_code
        == 409
    )
    assert (
        client.put(
            route(p, "/blocks/title"), json={"version": 1, "text": "过时编辑"}
        ).status_code
        == 409
    )
    assert (
        client.post(route(p, "/blocks/title/approve"), json={"version": 2}).status_code
        == 200
    )
    assert (
        client.put(
            route(p, "/blocks/title"), json={"version": 2, "text": "覆盖审核"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            route(p, "/jobs"),
            json={"key": "approved-target", "target": "title", "version": 2},
        ).status_code
        == 409
    )


def test_source_update_invalidates_approval_and_requires_fresh_generation(setup):
    app, client, p = setup
    client.post(route(p, "/blocks/title/approve"), json={"version": 1})
    source = p["sources"][0]
    response = client.put(
        route(p, f"/sources/{source['id']}"),
        json={"version": 1, "text": "新的资料：产品改为 200g 袋装。"},
    )
    b = response.json()["blocks"]["title"]
    assert b["stale"] and b["approval"] is None
    assert (
        client.post(route(p, "/blocks/title/approve"), json={"version": 1}).status_code
        == 409
    )
    client.post(
        route(p, "/jobs"),
        json={"key": "refresh-title", "target": "title", "version": 1},
    )
    fresh = client.get(route(p)).json()["blocks"]["title"]
    assert not fresh["stale"] and fresh["citations"][0]["source_version"] == 2
    assert (
        client.post(route(p, "/blocks/title/approve"), json={"version": 2}).status_code
        == 200
    )


def test_unrelated_sources_do_not_invalidate_block(setup):
    app, client, p = setup
    sid = uid()
    app.state.store.mutate(
        p["id"],
        lambda project: project["sources"].append(
            {"id": sid, "title": "other", "text": "其他资料", "version": 1}
        ),
    )
    response = client.put(
        route(p, f"/sources/{sid}"), json={"version": 1, "text": "其他资料已更新"}
    )
    assert all(not b["stale"] for b in response.json()["blocks"].values())


def test_background_job_cannot_overwrite_concurrent_source_edit(tmp_path):
    app = None

    def changing_generator(project, target, instruction, mode):
        app.state.store.mutate(
            project["id"],
            lambda p: p["sources"][0].update(text="资料在生成中改变", version=2),
        )
        return generate(project, target, instruction, mode)

    app = create_app(tmp_path, TOKEN, generator=changing_generator)
    with TestClient(app, headers=AUTH) as client:
        p = client.post("/api/demo").json()
        client.post(
            route(p, "/jobs"),
            json={"key": "concurrent-source", "target": "title", "version": 1},
        )
        job = client.get(route(p, "/jobs")).json()[0]
        assert job["status"] == "failed" and "资料已更新" in job["error"]
        assert client.get(route(p)).json()["blocks"]["title"]["version"] == 1
        assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409


def test_failed_job_retry_and_restart_recovery(tmp_path):
    attempts = []

    def flaky(project, target, instruction, mode):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("private connection string must not leak")
        return generate(project, target, instruction, mode)

    app = create_app(tmp_path, TOKEN, generator=flaky)
    with TestClient(app, headers=AUTH) as client:
        p = client.post("/api/demo").json()
        job = client.post(
            route(p, "/jobs"),
            json={"key": "retry-failed-job", "target": "post", "version": 1},
        ).json()
        failed = client.get(route(p, "/jobs")).json()[0]
        assert failed["status"] == "failed" and "private" not in failed["error"]
        assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 202
        assert client.get(route(p, "/jobs")).json()[0]["status"] == "succeeded"
        with app.state.store.connection() as db:
            interrupted = app.state.store.job(job["id"], db)
            interrupted["status"] = "running"
            app.state.store.save_job(interrupted, db)
    recovered = Store(tmp_path / "studio.sqlite3").job(job["id"])
    assert recovered["status"] == "failed" and "重启" in recovered["error"]


def test_new_project_generation_is_idempotent_and_protected(setup):
    app, client, _ = setup
    p = client.post(
        "/api/projects",
        json={
            "name": "新品内容",
            "brand": "测试品牌",
            "source_text": "这是一款 250g 的咖啡豆。",
        },
    ).json()
    request = {"key": "initial-generate"}
    assert client.post(route(p, "/jobs"), json=request).status_code == 202
    assert len(client.get(route(p)).json()["blocks"]) == 6
    assert (
        client.post(
            route(p, "/jobs"), json={**request, "instruction": "不同请求"}
        ).status_code
        == 409
    )
    assert (
        client.post(route(p, "/jobs"), json={"key": "another-full-pack"}).status_code
        == 409
    )


def test_concurrent_edits_accept_only_one_version(setup):
    app, client, p = setup

    def edit(n):
        return client.put(
            route(p, "/blocks/title"), json={"version": 1, "text": f"标题 {n}"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(edit, [1, 2]))
    assert sorted(codes) == [200, 409]


def test_empty_input_and_security_headers(setup):
    app, client, p = setup
    assert (
        client.put(
            route(p, "/blocks/title"), json={"version": 1, "text": "  "}
        ).status_code
        == 422
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
