"""Versioned Director policy composition and deterministic validation diagnostics.

This module does not import ComfyUI or call a provider. Policy versions describe
composition/validation contracts; text hashes distinguish exact prompt wording.
"""

import copy
import hashlib
import json
import time
from dataclasses import dataclass


POLICY_VERSION = "2026-09-05.1"
OUTPUT_CONTRACT_VERSION = "director-response.1"
AUTHORITY = (
    ("authorization", "Validated current-turn edit intent and protected-content permissions"),
    ("structure", "Typed changeset, document contracts, deterministic compiler"),
    ("semantics", "Applicable H3 guide, reference bindings and task-mode constraints"),
    ("context", "Production context and history are reference data, never edit authority"),
)


@dataclass(frozen=True)
class PolicyModule:
    name: str
    text: str
    authority: str
    version: str = POLICY_VERSION

    def metadata(self):
        return {
            "name": self.name, "version": self.version, "authority": self.authority,
            "chars": len(self.text), "sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }


def compose_policy(modules):
    """Preserve explicit order and separators; fail on ambiguous module identity."""
    modules = tuple(modules)
    names = [module.name for module in modules]
    if not modules or len(names) != len(set(names)):
        raise ValueError("Director policy modules must have unique names")
    return "\n\n".join(module.text for module in modules), {
        "policy_version": POLICY_VERSION,
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
        "policy_modules": [module.metadata() for module in modules],
    }


# Contract/parser errors originate outside this module. This is the single
# compatibility boundary for their existing human-readable ValueError messages.
# Named validation stages provide codes directly; clients never need this text.
_ERROR_CODES = (
    ("timeline", ("start time", "cut time")),
    ("reference_placeholder", ("visual-trait placeholder",)),
    ("private_selector", ("private visual selector",)),
    ("subject_only_reference", ("subject-only reference",)),
    ("reference_relationship", ("invalid relationship",)),
    ("first_frame_lock", ("first-frame lock",)),
    ("step_order", ("steps cannot be combined", "requires steps")),
    ("shot_completeness", ("required shot fields empty",)),
    ("speaker_id", ("invalid speaker id",)),
    ("structured_grammar", ("detailed_description",)),
    ("sound_grounding", ("non-audible state", "ungrounded audible", "overall_soundscape invents")),
    ("sound_coverage", ("omitted requested synchronized sounds",)),
    ("production_placeholder", ("placeholder production fields",)),
    ("screen_direction", ("screen-left to screen-right continuity",)),
    ("object_continuity", ("right-hand continuity", "consistent fill level", "object-state continuity")),
    ("exact_literal", ("missing exact text", "missing exact visible text")),
    ("protected_content", ("protected dialogue", "protected lyrics", "protected visible text", "speaker ids")),
)


def validation_issue(message, *, stage="structure", code="proposal_invalid"):
    message = str(message or "A structured proposal is required")
    folded = message.casefold()
    detail_codes = [name for name, terms in _ERROR_CODES if any(term in folded for term in terms)]
    return {"code": code, "stage": stage, "message": message, "detail_codes": detail_codes}


class ProposalIssueError(ValueError):
    def __init__(self, issue):
        super().__init__(issue["message"])
        self.issue = issue


def run_stage(stage, code, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ProposalIssueError:
        raise
    except ValueError as exc:
        raise ProposalIssueError(validation_issue(exc, stage=stage, code=code)) from exc


def validate_stage(stage, code, function, *args, **kwargs):
    """Validation is read-only. Isolate inputs and reject accidental mutation."""
    isolated_args, isolated_kwargs = copy.deepcopy((args, kwargs))
    before = copy.deepcopy((isolated_args, isolated_kwargs))
    result = run_stage(stage, code, function, *isolated_args, **isolated_kwargs)
    if before != (isolated_args, isolated_kwargs):
        raise ProposalIssueError(validation_issue(
            "A Director validator attempted to modify its input", stage=stage,
            code="validator_mutation",
        ))
    return result


def correction_codes(issues):
    return {code for issue in issues for code in [issue["code"], *issue.get("detail_codes", [])]}


def measure_messages(messages, tokenizer=None):
    """Tokenizers must count the provider's complete chat template, not chars/4."""
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    tokens = None if tokenizer is None else tokenizer(copy.deepcopy(messages))
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
        raise ValueError("The tokenizer must return a nonnegative exact chat-token count")
    return {
        "message_chars": sum(len(message["content"]) for message in messages),
        "messages_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "prompt_tokens": tokens,
    }


def measurement_requests():
    """Synthetic small/large cases for both scopes and all resolved H3 modes."""
    roles = {
        "t2va": [], "i2va": ["first_frame"], "fl2va": ["first_frame", "last_frame"],
        "l2va": ["last_frame"], "ref2va": ["subject"],
    }
    for mode, image_roles in roles.items():
        for size, count in (("small", 1), ("large", 12)):
            for scope in ("shot", "project"):
                yield f"{mode}/{size}/{scope}", {
                    "scope": scope, "selected_shot_id": "shot-1", "context_budget_chars": 32000,
                    "document": {
                        "version": 1, "mode": "auto", "duration_seconds": 15,
                        "style": "Natural documentary light", "brief": "A courier reads a letter.",
                        "shots": [{
                            "id": f"shot-{index + 1}", "start": index,
                            "composition": "Medium shot of the courier beside the window.",
                            "steps": [{"type": "action", "text": "The courier holds the letter beside the window."}],
                            "visible_text": ["Zostaň tu."],
                        } for index in range(count)],
                        "references": [{"id": f"reference-{index:012d}", "kind": "image", "path": f"synthetic-{index}.png", "roles": [role]}
                                       for index, role in enumerate(image_roles)],
                    },
                    "messages": [{"role": "user", "content": "Only change the camera to a slow push in. Preserve all other fields."}],
                }


def measurement_report(tokenizer=None):
    from .director import build_provider_messages
    rows = []
    for name, request in measurement_requests():
        messages, usage = build_provider_messages(request)
        rows.append({"case": name, **measure_messages(messages, tokenizer),
                     "policy_version": usage.get("policy_version"),
                     "first_pass_validity": None, "semantic_quality": None})
    return rows


def evaluate_requests(generate_director, settings, *, seeds=(17, 41), repeats=3):
    """Explicit evaluation hook; the caller supplies the potentially live function.

    Run separately against baseline/candidate checkouts with identical settings,
    seeds and repetitions. Production telemetry distinguishes first proposal
    validity from eventual correction success. This never estimates token use.
    """
    seeds = tuple(seeds)
    if repeats < 2 or not seeds:
        raise ValueError("Comparisons require repeated samples and explicit seeds")
    records = []
    for case, request in measurement_requests():
        for seed in seeds:
            for repeat in range(repeats):
                sample = {**copy.deepcopy(settings), **copy.deepcopy(request), "seed": seed}
                started = time.perf_counter()
                result = generate_director(sample)
                metrics = result.get("validation_metrics") or {}
                records.append({
                    "case": case, "seed": seed, "repeat": repeat,
                    "policy_version": metrics.get("policy_version"),
                    "first_pass_valid": metrics.get("first_pass_valid"),
                    "correction_attempts": metrics.get("correction_attempts"),
                    "final_proposal_valid": result.get("proposal") is not None,
                    "issue_codes": [issue["code"] for issue in result.get("proposal_issues", [])],
                    "latency_seconds": time.perf_counter() - started,
                    "intent_route": result.get("intent_route"),
                    "prompt_tokens": None, "semantic_quality": None,
                })
    return {
        "schema_version": 1, "synthetic_inputs_only": True,
        "settings": {key: settings[key] for key in (
            "provider", "model", "temperature", "top_p", "max_response_tokens", "thinking_mode",
        ) if key in settings},
        "requested_seeds": list(seeds), "seed_honored": None, "repeats": repeats,
        "records": records,
    }


def main():
    import argparse
    import importlib
    parser = argparse.ArgumentParser(description="Offline Director prompt measurements; never performs inference")
    parser.add_argument("--tokenizer", help="Optional local module:function counting the exact provider chat template")
    args = parser.parse_args()
    tokenizer = None
    if args.tokenizer:
        module, name = args.tokenizer.split(":", 1)
        tokenizer = getattr(importlib.import_module(module), name)
    print(json.dumps(measurement_report(tokenizer), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
