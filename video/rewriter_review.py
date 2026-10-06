"""Evaluate imported specialist rewrites without changing the authored document."""
from collections import Counter
import json
import re

from .contracts import normalize_document
from .compiler import compile_prompt


def review_rewrite(document, draft, adapter="8b"):
    document = normalize_document(document)
    if adapter not in {"8b", "omni"}:
        raise ValueError("Choose the 8B or Omni rewriter adapter")
    if not isinstance(draft, str) or not draft.strip() or len(draft) > 24000:
        raise ValueError("Paste a nonempty rewrite of at most 24,000 characters")
    metadata = {}
    if draft.lstrip().startswith("{"):
        metadata = json.loads(draft)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("enhanced_prompt"), str):
            raise ValueError("Rewriter JSON must contain an enhanced_prompt string")
        draft = metadata["enhanced_prompt"]
    draft = draft.strip()
    if not draft:
        raise ValueError("The rewritten prompt is empty")
    issues = []
    if adapter == "8b" and document["resolved_mode"] == "ref2va":
        issues.append("The 8B adapter does not support REF2VA; use Omni with the same references.")
    fields = (["subject_definitions", "summary", "retention_analysis", "detailed_description"]
              if document["resolved_mode"] == "ref2va" else ["integrated_multimodal_description"])
    fields += ["overall_soundscape", "non_diegetic_music"]
    positions = [draft.find(field) for field in fields]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        issues.append("Expected MiniMax prompt sections are missing or out of order.")
    baseline = compile_prompt(document, use_override=False)
    # Compare complete dialogue blocks, retaining language, punctuation and
    # cut markers. This is an evaluation gate, never edit authorization.
    dialogue = lambda text: Counter(re.findall(r"<d>(.*?)</d>", text, re.S))
    if dialogue(draft) != dialogue(baseline):
        issues.append("Dialogue or lyrics differ from the structured document; preserve every complete spoken block.")
    for shot in document["shots"]:
        for text in shot.get("visible_text", []):
            if text and text not in draft:
                issues.append("Existing visible text is missing or changed.")
                break
    expected_speakers = set(re.findall(r"\bS\d+\b", baseline))
    if expected_speakers != set(re.findall(r"\bS\d+\b", draft)):
        issues.append("Speaker IDs differ from the structured document.")
    if draft.count("<d>") != draft.count("</d>"):
        issues.append("Dialogue tags are unbalanced.")
    return {"draft": draft, "baseline": baseline, "issues": list(dict.fromkeys(issues)),
            "eligible_for_proposal": not issues, "adapter": adapter,
            "mode": document["resolved_mode"],
            "notice": "Structural checks are not a quality score. Review timing, identity, reference order and invented details. Importing never changes shots or the generation prompt."}
