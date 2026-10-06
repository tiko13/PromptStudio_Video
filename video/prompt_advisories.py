"""Non-mutating authoring advice and explicit dialogue continuity links."""
import re
from .contracts import PromptDocumentError, effective_duration


def link_dialogue(document):
    """Annotate a normalized compiler copy; never rewrite the spoken strings."""
    groups = {}
    for index, shot in enumerate(document["shots"]):
        for step in shot["steps"]:
            if step.get("type") == "dialogue" and step.get("utterance_id"):
                groups.setdefault(step["utterance_id"], []).append((index, step))
    for key, segments in groups.items():
        for (left_index, left), (right_index, right) in zip(segments, segments[1:]):
            if right_index != left_index + 1:
                raise PromptDocumentError(f"Dialogue link '{key}' must connect consecutive shots, once per shot")
            if any(left[field] != right[field] for field in ("speaker_id", "language", "performance", "voiceover", "offscreen")):
                raise PromptDocumentError(f"Dialogue link '{key}' must keep the same speaker, language and performance")
            if left.get("cutoff"):
                raise PromptDocumentError(f"Dialogue link '{key}' cannot continue after a cutoff")
            left["_continues_out"] = True
            right["_continues_in"] = True


def prompt_advisories(document):
    result = []
    duration = effective_duration(document)
    for index, shot in enumerate(document["shots"]):
        end = document["shots"][index + 1]["start"] if index + 1 < len(document["shots"]) else duration
        available = end - shot["start"]
        dialogue = [step for step in shot["steps"] if step.get("type") == "dialogue"]
        words = sum(len(re.findall(r"\S+", step["text"])) for step in dialogue)
        if words / max(available, 0.01) > 3.5:
            result.append({"code": "dialogue_density", "shot_id": shot["id"],
                           "message": f"Shot {index + 1}: {words} spoken words in {available:.1f}s may feel rushed. This is an estimate; language and delivery matter."})
        for step in dialogue:
            if step.get("crosses_cut") and not step.get("utterance_id"):
                result.append({"code": "unlinked_dialogue", "shot_id": shot["id"],
                               "message": f"Shot {index + 1}: assign the same dialogue link to the segments on both sides of the cut."})
            if step.get("utterance_id"):
                count = sum(s.get("utterance_id") == step["utterance_id"] for sh in document["shots"] for s in sh["steps"])
                if count == 1:
                    result.append({"code": "incomplete_dialogue_link", "shot_id": shot["id"],
                                   "message": f"Shot {index + 1}: dialogue link '{step['utterance_id']}' has only one segment."})
        action = " ".join(step.get("text", "") for step in shot["steps"] if step.get("type") == "action")
        if (shot.get("camera") or {}).get("type") == "Static Shot" and re.search(
            r"\bcamera\s+(?:slowly\s+)?(?:pans?|trucks?|zooms?|pushes|pulls|orbits?|tracks?)\b", action, re.I
        ):
            result.append({"code": "camera_conflict", "shot_id": shot["id"],
                           "message": f"Shot {index + 1}: Static Shot conflicts with camera movement in the action. Review the camera field."})
    if duration > 15.2:
        result.append({"code": "long_clip", "message": "This clip exceeds H3's usual 15-second range. Consider generating a shorter clip and using Continue video."})
    return result
