import json

import httpx
import pytest

from raywork.generator import generate, validate_blocks


@pytest.fixture
def project():
    return {
        "brief": {"brand": "示例咖啡", "tone": "克制", "avoid": "不编造事实"},
        "sources": [
            {"id": "source-1", "version": 3, "text": "产品是 250g 中度烘焙咖啡豆。"}
        ],
        "blocks": {},
    }


@pytest.mark.parametrize(
    "defect",
    [
        "unknown-source",
        "invented-quote",
        "empty-quote",
        "missing-block",
        "bad-citation",
        "bad-block",
    ],
)
def test_rejects_unverifiable_outputs(project, defect):
    blocks, _ = generate(project)
    if defect == "unknown-source":
        blocks[0]["citations"][0]["source_id"] = "invented"
    if defect == "invented-quote":
        blocks[0]["citations"][0]["quote"] = "销量第一"
    if defect == "empty-quote":
        blocks[0]["citations"][0]["quote"] = " "
    if defect == "missing-block":
        blocks.pop()
    if defect == "bad-citation":
        blocks[0]["citations"][0] = 1
    if defect == "bad-block":
        blocks[0] = None
    with pytest.raises(ValueError):
        validate_blocks(blocks, project)


def provider(monkeypatch, handler):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "operator-selected-model")
    original = httpx.Client
    monkeypatch.setattr(
        "raywork.generator.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


def test_real_adapter_uses_structured_responses_and_records_usage(project, monkeypatch):
    blocks, _ = generate(project, target="title")

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] and body["store"] is False
        assert json.loads(body["input"])["required_blocks"] == {"title": "图文标题"}
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
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    provider(monkeypatch, handler)
    monkeypatch.setenv("INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("OUTPUT_USD_PER_MILLION", "2")
    output, usage = generate(project, "title", "标题更具体", mode="openai")
    assert output[0]["citations"][0]["source_version"] == 3
    assert usage["input_tokens"] == 100 and usage[
        "estimated_cost_usd"
    ] == pytest.approx(0.00016)


@pytest.mark.parametrize(
    "status,result",
    [
        (429, {"error": "private provider details"}),
        (200, {"status": "incomplete"}),
        (
            200,
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "No"}],
                    }
                ],
            },
        ),
    ],
)
def test_provider_errors_never_look_like_success(project, monkeypatch, status, result):
    provider(monkeypatch, lambda request: httpx.Response(status, json=result))
    with pytest.raises((ValueError, KeyError)):
        generate(project, "title", mode="openai")


def test_live_mode_requires_explicit_credentials(project, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError, match="服务端配置"):
        generate(project, mode="openai")
