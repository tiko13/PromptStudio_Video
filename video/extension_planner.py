"""Backend LLM planning for native MiniMax H3 video extensions."""

from __future__ import annotations

from .compiler import compile_prompt
from .continuation import (
    build_extension_authoring_document,
    build_continuation_document,
    continuation_director_context,
    continuation_frame_plan,
)
from .contracts import PromptDocumentError, normalize_document
from .director import director_chat, preview_changeset, _validate_extension_plan_timeline


def _planning_instruction(brief):
    return (
        "Completely rewrite the entire production for this extension as a structured MiniMax H3 "
        "plan covering only the newly generated tail. Start authored Shot 1 immediately after the exact "
        "native Soft AV boundary described in continuation_context. Do not replay, restate, or hold "
        "the source ending. Preserve established identity, wardrobe, props, geometry, lighting, "
        "exposure, active camera motion, momentum, ambience, and sounds unless the requested future "
        "development explicitly changes them. Convert every requested spoken or sung line into a "
        "dialogue step with a concrete speaker, stable speaker_id, language, performance, delivery, "
        "and verbatim text so the deterministic compiler emits MiniMax <d> syntax. Put visible action "
        "in action steps and audible consequences in synchronized sounds. Choose one continuous shot "
        "unless a later cut adds useful new information. Do not create reference tokens or media "
        "semantics for the source; its pixels and audio are supplied by the protected latent handoff. "
        "The user's exact continuation request follows:\n<extension_request>\n"
        + str(brief or "").strip()
        + "\n</extension_request>"
    )


def plan_extension(data, progress_callback=None):
    """Use the configured shared LLM provider to author one reusable extension document."""
    if not isinstance(data, dict):
        raise ValueError("Extension planning request must be an object")
    brief = str(data.get("brief") or "").strip()
    if not brief:
        raise ValueError("Describe what should happen in the extension")
    parent = normalize_document(data.get("document") or {})
    authored = build_extension_authoring_document(
        parent,
        brief,
        data.get("duration_seconds", 5),
    )
    timing = continuation_frame_plan(data.get("duration_seconds", 5))
    continuation_context = continuation_director_context(
        parent,
        data.get("source_effective_duration", 0),
    )
    continuation_context["authored_tail_duration"] = timing["delivered_duration"]
    request = {
        **data,
        "document": authored,
        "brief": brief,
        "scope": "project",
        "selected_shot_id": "",
        "attachments": [],
        "pending_plan": None,
        "continuation_context": continuation_context,
        "extension_planning": True,
        "require_proposal": True,
        "messages": [{"role": "user", "content": _planning_instruction(brief)}],
    }
    result = director_chat(request, progress_callback)
    if result.get("status") == "needs_clarification":
        raise ValueError(result.get("message") or "The extension plan needs clarification")
    proposal = result.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError(
            result.get("proposal_error")
            or result.get("message")
            or "The Director did not return a valid extension plan"
        )
    planned = preview_changeset(authored, proposal, request_data=request)["document"]
    _validate_extension_plan_timeline(planned, request)
    if planned.get("resolved_mode") != "t2va" or planned.get("references"):
        raise PromptDocumentError(
            "A latent-only extension plan cannot introduce media references"
        )
    # Apply the exact context-prefix/grid transform once before returning the
    # authored plan. This protects callers even if a future planner validation
    # stops catching a late cut or timed cue near the trimmed boundary.
    build_continuation_document(
        parent,
        brief,
        data.get("duration_seconds", 5),
        extension_document=planned,
    )
    return {
        "valid": True,
        "document": planned,
        "compiled_prompt": compile_prompt(planned),
        "message": str(result.get("message") or "Extension plan prepared."),
        "continuation_context": continuation_context,
        "timing": timing,
        "context_usage": result.get("context_usage"),
    }
