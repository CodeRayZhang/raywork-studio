import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from raywork.api import create_app
from raywork.generator import active_rules, generate

TOKEN = "memory-test-token-not-for-production"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path, TOKEN)
    with TestClient(app, headers=AUTH) as c:
        p = c.post("/api/demo").json()
        yield app, c, p, f"/api/projects/{p['id']}"


def propose(c, url, text="标题避免夸张承诺", scope="title", **kw):
    response = c.post(url + "/memory", json={"text": text, "scope": scope, **kw})
    assert response.status_code == 200, response.text
    return response.json()["memory"][-1]


def decision(c, url, rule, status="active"):
    return c.post(
        url + "/memory/" + rule["id"],
        json={"version": rule["version"], "status": status},
    )


def test_comment_to_rule_lifecycle_and_history(workspace):
    app, c, p, url = workspace
    p = c.post(
        url + "/blocks/title/comments", json={"version": 1, "text": "别用夸张承诺"}
    ).json()
    comment = p["blocks"]["title"]["comments"][0]
    rule = propose(c, url, comment_id=comment["id"])
    assert active_rules(c.get(url).json()) == []
    p = decision(c, url, rule).json()
    assert p["blocks"]["title"]["comments"][0]["resolved"] is False
    assert p["memory"][0]["origin"]["text"] == comment["text"]
    assert decision(c, url, rule).status_code == 409
    original_post = p["blocks"]["post"]
    c.post(url + "/jobs", json={"key": "memory-first", "target": "title", "version": 1})
    p = c.get(url).json()
    assert p["blocks"]["title"]["memory_context"][0]["id"] == rule["id"]
    assert p["blocks"]["post"] == original_post
    assert decision(c, url, p["memory"][0], "retired").status_code == 200
    c.post(
        url + "/jobs", json={"key": "memory-second", "target": "title", "version": 2}
    )
    b = c.get(url).json()["blocks"]["title"]
    assert b["memory_context"] == []
    assert b["history"][-1]["memory_context"][0]["id"] == rule["id"]
    # Old databases without a memory field remain readable; rules survive reopening.
    app2 = create_app(app.state.store.path.parent, TOKEN)
    with TestClient(app2, headers=AUTH) as reopened:
        assert reopened.get(url).json()["memory"][0]["status"] == "retired"


def test_import_is_explicit_pending_deduplicated_snapshot(workspace):
    app, c, p, url = workspace
    rule = propose(c, url)
    decision(c, url, rule)
    other = c.post("/api/demo").json()
    dest = f"/api/projects/{other['id']}"
    assert active_rules(other) == []
    imported = c.post(dest + "/memory/import", json={"project_id": p["id"]}).json()
    copied = imported["memory"][0]
    assert copied["status"] == "pending" and copied["id"] != rule["id"]
    assert active_rules(imported) == []
    assert (
        c.post(dest + "/memory/import", json={"project_id": p["id"]}).status_code == 409
    )
    decision(c, dest, copied)
    decision(c, url, c.get(url).json()["memory"][0], "retired")
    assert len(active_rules(c.get(dest).json())) == 1
    app.state.store.mutate(other["id"], lambda d: d["brief"].update(brand="其他客户"))
    assert (
        c.post(dest + "/memory/import", json={"project_id": p["id"]}).status_code == 422
    )


def test_reviewer_cannot_read_or_change_memory(workspace):
    app, c, p, url = workspace
    rule = propose(c, url)
    decision(c, url, rule)
    c.post(
        url + "/jobs", json={"key": "memory-review", "target": "title", "version": 1}
    )
    share = c.post(url + "/share").json()
    auth = {"Authorization": "Bearer " + share["token"]}
    view = c.get("/api/review", headers=auth).json()
    assert "memory" not in view
    assert "memory_context" not in view["blocks"]["title"]
    assert (
        c.post(url + "/memory", headers=auth, json={"text": "任意规则"}).status_code
        == 401
    )

    assert (
        c.post(
            url + "/memory/" + rule["id"],
            headers=auth,
            json={"version": 1, "status": "active"},
        ).status_code
        == 401
    )


def test_export_preserves_rule_snapshot_after_retirement(workspace):
    app, c, p, url = workspace
    rule = propose(c, url)
    active = decision(c, url, rule).json()["memory"][0]
    c.post(
        url + "/jobs", json={"key": "memory-export", "target": "title", "version": 1}
    )
    p = c.get(url).json()
    for key, block in p["blocks"].items():
        assert (
            c.post(
                url + f"/blocks/{key}/approve", json={"version": block["version"]}
            ).status_code
            == 200
        )
    decision(c, url, active, "retired")
    result = c.get(url + "/export")
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        project = json.loads(archive.read("manifest.json"))["project"]
    assert project["memory"][0]["status"] == "retired"
    snapshot = project["blocks"]["title"]["memory_context"][0]
    assert snapshot["id"] == active["id"] and snapshot["version"] == active["version"]
    assert project["blocks"]["title"]["approval"]["version"] == 2


def test_rule_validation_and_project_scoped_origin(workspace):
    app, c, p, url = workspace
    assert c.post(url + "/memory", json={"text": "  "}).status_code == 422
    assert (
        c.post(url + "/memory", json={"text": "x", "comment_id": "foreign"}).status_code
        == 404
    )
    propose(c, url)
    assert (
        c.post(
            url + "/memory", json={"text": "标题避免夸张承诺", "scope": "title"}
        ).status_code
        == 409
    )
    assert (
        c.post(url + "/memory", json={"text": "x", "scope": "invalid"}).status_code
        == 422
    )


def test_rule_change_during_generation_does_not_overwrite(tmp_path):
    app = None

    def changing(project, target, instruction, mode):
        def change(p):
            p["memory"][0].update(status="retired", version=3)

        app.state.store.mutate(project["id"], change)
        return generate(project, target, instruction, mode)

    app = create_app(tmp_path, TOKEN, generator=changing)
    with TestClient(app, headers=AUTH) as c:
        p = c.post("/api/demo").json()
        url = f"/api/projects/{p['id']}"
        rule = propose(c, url)
        decision(c, url, rule)
        c.post(
            url + "/jobs",
            json={"key": "concurrent-rule", "target": "title", "version": 1},
        )
        assert c.get(url + "/jobs").json()[0]["status"] == "failed"
        assert c.get(url).json()["blocks"] == p["blocks"]


def test_real_prompt_filters_unconfirmed_retired_and_other_modules(monkeypatch):
    import httpx

    project = {
        "brief": {"brand": "咖啡"},
        "sources": [{"id": "s", "text": "250g咖啡豆", "version": 1}],
        "blocks": {},
        "memory": [
            {"id": str(i), "text": text, "scope": scope, "status": status, "version": 1}
            for i, (text, scope, status) in enumerate(
                [
                    ("标题短句", "title", "active"),
                    ("克制", "all", "active"),
                    ("待确认秘密", "all", "pending"),
                    ("停用秘密", "all", "retired"),
                    ("正文专用", "post", "active"),
                ]
            )
        ],
    }
    blocks, _ = generate(project, "title")
    project["blocks"]["title"] = {
        **blocks[0],
        "version": 2,
        "comments": [{"text": "待确认秘密"}],
        "history": [{"memory_context": [{"text": "停用秘密"}]}],
        "usage": {"cost": 10},
    }

    def handler(req):
        payload = json.loads(req.content)
        data = json.loads(payload["input"])
        assert [r["text"] for r in data["confirmed_editorial_rules"]] == [
            "标题短句",
            "克制",
        ]
        assert "秘密" not in payload["input"] and "正文专用" not in payload["input"]
        assert set(data["current_block"]) == {"key", "text", "citations", "version"}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"blocks": blocks}),
                            }
                        ],
                    }
                ],
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "raywork.generator.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    generate(project, "title", mode="openai")
