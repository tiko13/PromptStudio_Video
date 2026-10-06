"""Regenerate the optional native H3 workflows from the legacy Turbo layout."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "workflows"
sys.path.insert(0, str(ROOT))
from video.sampling_profiles import select_profile


def build():
    legacy_path = PATH / "[PSV] MiniMax H3 Turbo.json"
    source = json.loads(legacy_path.read_text(encoding="utf-8"))
    # Safe default for newly installed legacy workflows; saved user graphs and
    # immutable generation snapshots are never rewritten by this script.
    attention = next(node for node in source["nodes"] if node["id"] == 26)
    attention["widgets_values"] = ["pytorch attention"]
    attention["widgets_values_named"] = {"attention": "pytorch attention"}
    legacy_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    variants = [
        ("MiniMax H3 Fast v2", "fast", "safe_dense"),
        ("MiniMax H3 Balanced v2", "balanced", "safe_dense"),
        ("MiniMax H3 Full quality v2", "full_quality", "safe_dense"),
        ("MiniMax H3 Sparse experiment", "full_quality", "experimental_sol"),
        ("MiniMax H3 TaoMate experiment", "experimental_taomate", "safe_dense"),
        ("FastH3 V2 experiment", "experimental_fasth3", "safe_dense"),
    ]
    for name, preset, backend in variants:
        workflow = copy.deepcopy(source)
        remove = {9, 12, 26}
        if preset.startswith("experimental_"):
            remove.add(2)
        workflow["nodes"] = [node for node in workflow["nodes"] if node["id"] not in remove]
        nodes = {node["id"]: node for node in workflow["nodes"]}
        profile = nodes[25]
        profile["type"] = "PSV_MiniMaxH3SamplingProfile"
        profile["title"] = "Prompt Studio MiniMax H3 Sampling Profile"
        profile["properties"] = {"Node name for S&R": profile["type"]}
        profile["inputs"] = profile["inputs"][:4]
        profile["widgets_values"] = [preset, backend]
        profile["widgets_values_named"] = {"preset": preset, "attention": backend}
        profile["outputs"].append({"name": "sampler", "type": "SAMPLER", "links": []})
        if preset == "experimental_fasth3":
            model = "MiniMax3/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
            nodes[1]["widgets_values"][0] = model
            nodes[1]["widgets_values_named"]["unet_name"] = model
        workflow["links"] = [link for link in workflow["links"] if link[1] not in remove and link[3] not in remove]
        workflow["links"].extend([[56, 25, 0, 10, 0, "MODEL"], [57, 25, 6, 15, 2, "SAMPLER"]])
        if preset == "experimental_taomate":
            # Keep the distilled states 0,16,33,49 of the shifted 50-point grid.
            scheduler = nodes[14]
            scheduler["type"] = "ManualSigmas"
            scheduler["title"] = "TaoMate distilled sigma schedule"
            scheduler["properties"] = {"Node name for S&R": "ManualSigmas"}
            scheduler["inputs"] = [{"name": "sigmas", "type": "STRING", "widget": {"name": "sigmas"}, "link": None}]
            sigmas = ", ".join(format(value, ".10g") for value in select_profile("t2va", 1344, 768, preset).sigmas)
            scheduler["widgets_values"] = [sigmas]
            scheduler["widgets_values_named"] = {"sigmas": sigmas}
            workflow["links"] = [link for link in workflow["links"] if link[3] != 14]
        for node in workflow["nodes"]:
            for item in node.get("inputs", []):
                item["link"] = None
            for item in node.get("outputs", []):
                item["links"] = []
        for link, source_id, source_slot, target_id, target_slot, _type in workflow["links"]:
            nodes[source_id]["outputs"][source_slot]["links"].append(link)
            nodes[target_id]["inputs"][target_slot]["link"] = link
        workflow["last_link_id"] = max(link[0] for link in workflow["links"])
        workflow["last_node_id"] = max(node["id"] for node in workflow["nodes"])
        (PATH / f"[PSV] {name}.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
