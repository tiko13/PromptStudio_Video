"""Add explicit typed Studio loaders to the bundled workflow sources."""
import json
from pathlib import Path


def add_adapter_loaders(graph):
    if any(node["type"] == "PSV_MiniMaxH3ReferenceAdapters" for node in graph["nodes"]):
        return
    director = next(node for node in graph["nodes"] if node["type"] == "PSV_MiniMaxH3Director")
    content_id = max(99, *(node["id"] for node in graph["nodes"])) + 1
    reference_id = content_id + 1
    link_id = max(99, *(link[0] for link in graph["links"])) + 1
    for link in graph["links"]:
        if link[1] == director["id"] and link[2] in (0, 1):
            link[1] = reference_id
    graph["links"].extend([
        [link_id, director["id"], 0, content_id, 0, "MODEL"],
        [link_id + 1, content_id, 0, reference_id, 0, "MODEL"],
        [link_id + 2, director["id"], 1, reference_id, 1, "CONDITIONING"],
        [link_id + 3, director["id"], 6, reference_id, 2, "STRING"],
    ])
    def node(node_id, kind, title, inputs, outputs, values):
        return {"id": node_id, "type": kind, "title": title, "pos": [1250, 900 + (node_id-content_id)*260],
                "size": [350, 240], "flags": {}, "order": len(graph["nodes"]), "mode": 0,
                "inputs": [{"name": name, "type": typ, "link": None} for name, typ in inputs],
                "outputs": [{"name": name, "type": typ, "links": []} for name, typ in outputs],
                "properties": {"Node name for S&R": kind}, "widgets_values": list(values.values()),
                "widgets_values_named": values}
    graph["nodes"].append(node(content_id, "KCPP_PromptStudioLoraLoader", "Content LoRAs · Type: MiniMax3",
        [("model", "MODEL")], [("model", "MODEL")], {"lora_type": "MiniMax3", "lora_stack_json": "[]"}))
    graph["nodes"].append(node(reference_id, "PSV_MiniMaxH3ReferenceAdapters", "RefMods / RefLoRAs · Type: MiniMax3",
        [("model", "MODEL"), ("positive", "CONDITIONING"), ("mode", "STRING")], [("model", "MODEL"), ("positive", "CONDITIONING")],
        {"reference_stack_json": "[]", "max_reference_tokens": 16384, "adapter": "None", "lora_strength": 1,
         "visual_strength": 1, "audio_strength": 1, "components": "all", "adapter_type": "MiniMax3"}))
    nodes = {node["id"]: node for node in graph["nodes"]}
    for item in graph["nodes"]:
        for port in item.get("outputs", []):
            port["links"] = []
    for link, origin, slot, target, target_slot, _ in graph["links"]:
        nodes[origin]["outputs"][slot]["links"].append(link)
        nodes[target]["inputs"][target_slot]["link"] = link
    graph["last_node_id"] = max(nodes)
    graph["last_link_id"] = max(link[0] for link in graph["links"])


if __name__ == "__main__":
    for path in (Path(__file__).resolve().parents[1] / "workflows").glob("*.json"):
        graph = json.loads(path.read_text(encoding="utf-8"))
        add_adapter_loaders(graph)
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
