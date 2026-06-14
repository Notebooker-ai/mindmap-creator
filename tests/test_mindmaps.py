"""Tests for MindmapCreator with a stubbed language model (no network)."""

from __future__ import annotations

import json
import tempfile

import pytest
from mindmap_creator import MindmapCreator
from open_notebook_creator_sdk import ContentBundle, CreationRequest, ModelRole
from open_notebook_creator_sdk.testing import assert_creator_compliant, assert_result_compliant


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    async def ainvoke(self, _):
        return _FakeResp(self._payload)


class _FakeRole(ModelRole):
    payload: str = ""

    def create_language(self, **_):
        return _FakeLLM(self.payload)


def _role(obj):
    return _FakeRole(provider="f", model="f", payload=json.dumps(obj))


_MERMAID = "mindmap\n  root((Topic))\n    A\n    B"


def test_static_compliance():
    assert_creator_compliant(MindmapCreator())


@pytest.mark.asyncio
async def test_generate_valid_mindmap():
    creator = MindmapCreator()
    payload = {"title": "Topic", "mermaid_syntax": _MERMAID, "description": "A map"}
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="Some content"),
            config={"max_depth": 5},
            models={"text": _role(payload)},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "SUCCESS"
        assert_result_compliant(creator, result)
        assert result.data["mermaid_syntax"].startswith("mindmap")
        assert result.data["title"] == "Topic"


@pytest.mark.asyncio
async def test_prepends_mindmap_header_when_missing():
    creator = MindmapCreator()
    payload = {"title": "T", "mermaid_syntax": "  root((R))\n    A"}
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="x"),
            models={"text": _role(payload)},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "SUCCESS"
        assert result.data["mermaid_syntax"].startswith("mindmap\n")


@pytest.mark.asyncio
async def test_strips_markdown_fences():
    creator = MindmapCreator()
    obj = {"title": "T", "mermaid_syntax": _MERMAID}
    fenced = "```json\n" + json.dumps(obj) + "\n```"
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="x"),
            models={"text": _FakeRole(provider="f", model="f", payload=fenced)},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "SUCCESS"
        assert result.data["title"] == "T"


@pytest.mark.asyncio
async def test_strips_mermaid_fence_inside_syntax():
    creator = MindmapCreator()
    payload = {"title": "T", "mermaid_syntax": "```mermaid\n" + _MERMAID + "\n```"}
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="x"),
            models={"text": _role(payload)},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "SUCCESS"
        assert result.data["mermaid_syntax"].startswith("mindmap")
        assert "```" not in result.data["mermaid_syntax"]


@pytest.mark.asyncio
async def test_empty_syntax_is_failure():
    creator = MindmapCreator()
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="x"),
            models={"text": _role({"title": "T", "mermaid_syntax": "   "})},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "FAILURE"


@pytest.mark.asyncio
async def test_invalid_json_is_failure():
    creator = MindmapCreator()
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(
            content=ContentBundle(text="x"),
            models={"text": _FakeRole(provider="f", model="f", payload="not json")},
            output_dir=td,
            artifact_id="a",
        )
        result = await creator.generate(req)
        assert result.status == "FAILURE"
        assert result.errors[0].phase == "parse"


@pytest.mark.asyncio
async def test_no_text_role_is_failure():
    creator = MindmapCreator()
    with tempfile.TemporaryDirectory() as td:
        req = CreationRequest(content=ContentBundle(text="x"), output_dir=td, artifact_id="a")
        result = await creator.generate(req)
        assert result.status == "FAILURE"
        assert result.errors[0].phase == "setup"
