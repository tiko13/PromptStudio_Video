"""Manual live-LLM audit matrix for Video Director turn routing.

Run from the plugin root with ComfyUI's Python while the configured local LLM
server is available. This file is deliberately not named test_*.py because it
performs live model inference.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COMFY_ROOT = Path(__file__).resolve().parents[3]
for import_root in (PLUGIN_ROOT, COMFY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from video.director import _classify_director_turn  # noqa: E402


CASES = [
    ("mutate_gets_up", "mutate", "She gets up and exits the room."),
    ("mutate_rises", "mutate", "She rises from the chair and exits frame left."),
    ("mutate_have_her", "mutate", "Have her rise and leave through the doorway."),
    ("mutate_fragment", "mutate", "Standing, crossing the room, then out through the door."),
    ("mutate_noun_phrase", "mutate", "A slow rise followed by one continuous exit."),
    ("mutate_polite", "mutate", "Could you have her get up and walk out?"),
    ("mutate_desire", "mutate", "I want her to stand and leave without a cut."),
    ("mutate_lets", "mutate", "Let's make the doorway exit one continuous take."),
    ("mutate_tentative", "mutate", "Maybe have her leave through the other door."),
    ("mutate_correction", "mutate", "No, slower, and keep the same outfit."),
    ("mutate_continue", "mutate", "Continue the action until she exits."),
    ("mutate_full_prompt", "mutate", "Write the full video prompt."),
    ("mutate_dialogue", "mutate", 'Add dialogue: "Wait for me."'),
    ("mutate_author_line", "mutate", "What should she say here?"),
    ("mutate_declarative_dialogue", "mutate", 'She whispers, "Do not follow me," and leaves.'),
    ("mutate_camera", "mutate", "Change this to a slow push-in as she stands."),
    ("mutate_pov", "mutate", "Switch this shot to first person."),
    ("discuss_feasibility", "discuss", "Can she stand and walk out in five seconds?"),
    ("discuss_comparison", "discuss", "Would it work better if she left more slowly?"),
    ("discuss_why", "discuss", "Why does the exit feel abrupt?"),
    ("discuss_improvement", "discuss", "How could I improve the pacing?"),
    ("discuss_opinion", "discuss", "Do you think she walks too fast?"),
    ("discuss_options", "discuss", "Give me three alternatives for her exit."),
    ("discuss_camera_choice", "discuss", "Which camera move would work best?"),
    ("discuss_description", "discuss", "Describe how she could leave the room."),
    ("discuss_critique_only", "discuss", "Do not change anything; just critique the motion."),
    ("discuss_next_idea", "discuss", "What could happen next?"),
    ("discuss_too_fast", "discuss", "Is the exit too fast?"),
    ("discuss_should_i", "discuss", "Should I add a cut before she leaves?"),
    ("clarify_do_that", "clarify", "Do that."),
    ("clarify_other", "clarify", "Use the other one."),
    ("clarify_fix", "clarify", "Fix it."),
]


FOLLOW_UP_CASES = [
    (
        "mutate_resolved_followup",
        "mutate",
        [
            {"role": "user", "content": "Should she stay seated or leave?"},
            {"role": "assistant", "content": "Leaving gives the shot a stronger ending."},
            {"role": "user", "content": "Do that, but make the exit slow."},
        ],
    ),
    (
        "mutate_selected_option",
        "mutate",
        [
            {"role": "user", "content": "Give me three exit ideas."},
            {"role": "assistant", "content": "1. Doorway. 2. Pass behind camera. 3. Cut on motion."},
            {"role": "user", "content": "The second one."},
        ],
    ),
    (
        "discuss_followup_question",
        "discuss",
        [
            {"role": "user", "content": "Could she leave through the doorway?"},
            {"role": "assistant", "content": "Yes, that is feasible in one take."},
            {"role": "user", "content": "Would that read better than a cut?"},
        ],
    ),
]


def settings(args):
    return {
        "llm_provider": args.provider,
        "kobold_url": args.url,
        "ollama_url": args.url,
        "ollama_model": args.model,
        "thinking_mode": "Disabled",
        "request_timeout": args.timeout,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="koboldcpp", choices=("koboldcpp", "ollama", "llamacpp"))
    parser.add_argument("--url", default="http://localhost:5001")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--case", action="append", dest="selected")
    args = parser.parse_args()
    selected = set(args.selected or [])
    failures = []

    work = [
        (name, expected, [{"role": "user", "content": prompt}])
        for name, expected, prompt in CASES
    ] + FOLLOW_UP_CASES
    for name, expected, messages in work:
        if selected and name not in selected:
            continue
        try:
            result = _classify_director_turn({
                **settings(args),
                "scope": "shot",
                "messages": messages,
            })
            passed = result["route"] == expected
            print(json.dumps({
                "case": name,
                "expected": expected,
                "actual": result["route"],
                "confidence": result["confidence"],
                "reason": result["reason"],
                "passed": passed,
            }, ensure_ascii=False))
            if not passed:
                failures.append(name)
        except Exception as exc:
            failures.append(name)
            print(json.dumps({"case": name, "exception": str(exc), "passed": False}))

    if failures:
        print(f"Failed {len(failures)} case(s): {', '.join(failures)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
