"""mindmap-creator: an Open Notebook creator that turns notebook content into a
mermaid mindmap (emitted as ``mindmap.v1``, rendered client-side by mermaid.js).
"""

import json
import re
from importlib import resources
from typing import ClassVar

from ai_prompter import Prompter
from loguru import logger
from open_notebook_creator_sdk import (
    BaseCreator,
    CreationError,
    CreationRequest,
    CreationResult,
    CreatorManifest,
    ModelRoleSpec,
)
from open_notebook_creator_sdk.schemas import MindmapV1
from pydantic import BaseModel, Field

__version__ = "0.1.0"


class MindmapConfig(BaseModel):
    max_depth: int = Field(
        default=5, ge=2, le=8, description="Maximum depth of the mindmap hierarchy"
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def _normalize_mermaid(syntax: str) -> str:
    """Strip an accidental ```mermaid fence and ensure a ``mindmap`` header."""
    syntax = _strip_fences(syntax).strip("\n")
    stripped = syntax.lstrip()
    if not stripped.startswith("mindmap"):
        syntax = "mindmap\n" + syntax
    return syntax


class MindmapCreator(BaseCreator):
    config_model: ClassVar[type] = MindmapConfig

    @property
    def manifest(self) -> CreatorManifest:
        return self.build_manifest(
            key="mindmaps",
            name="Mindmaps",
            version=__version__,
            description="LLM-generated mermaid mindmaps rendered in the browser.",
            sdk_compat=">=0.1,<1",
            emits=["mindmap.v1"],
            model_roles=[
                ModelRoleSpec(
                    key="text",
                    kind="language",
                    requires=["structured_json"],
                    description="LLM that designs the mindmap structure.",
                )
            ],
            icon="network",
        )

    async def generate(self, request: CreationRequest) -> CreationResult:
        cfg = MindmapConfig.model_validate(request.config)
        role = request.models.get("text")
        if role is None:
            return CreationResult(
                status="FAILURE",
                schema_id="mindmap.v1",
                data={},
                errors=[CreationError(phase="setup", message="missing 'text' model role")],
                user_message="No language model was provided for mindmap generation.",
            )

        template = resources.files("mindmap_creator.prompts").joinpath(
            "mindmap.jinja"
        ).read_text()
        prompt = Prompter(template_text=template).render(
            {
                "content": request.content.text,
                "max_depth": cfg.max_depth,
                "instructions": request.instructions,
            }
        )
        llm = role.create_language(structured={"type": "json"}, max_tokens=4000)
        resp = await llm.ainvoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        try:
            parsed = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as e:
            logger.error(f"mindmaps: non-JSON response: {e}")
            return CreationResult(
                status="FAILURE",
                schema_id="mindmap.v1",
                data={},
                errors=[CreationError(phase="parse", message=f"invalid JSON: {e}", retryable=True)],
                user_message="The model returned an unparseable response. Please retry.",
            )

        syntax = parsed.get("mermaid_syntax", "") if isinstance(parsed, dict) else ""
        if not isinstance(syntax, str) or not syntax.strip():
            return CreationResult(
                status="FAILURE",
                schema_id="mindmap.v1",
                data={},
                errors=[CreationError(phase="generate", message="no mindmap produced")],
                user_message="No mindmap could be generated from this content.",
            )

        data = MindmapV1(
            title=parsed.get("title") or "Mindmap",
            mermaid_syntax=_normalize_mermaid(syntax),
            description=parsed.get("description"),
        ).model_dump()

        return CreationResult(
            status="SUCCESS",
            schema_id="mindmap.v1",
            data=data,
        )
