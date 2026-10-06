"""Observe a Studio-queued run; never submit, unload models, or alter settings.

Run with the ComfyUI interpreter. Start before/while a run is queued:
  python tools/observe_h3_benchmark.py --prompt-id ID --label dense --output result.json
Compare the same prompt, canvas, duration and seed across separate workflows.
Record a cold run separately, then repeats; benchmark one optimization at a time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def summarize(history, samples):
    graph = history.get("prompt", [None, None, {}])[2]
    messages = history.get("status", {}).get("messages", [])
    starts = [value.get("timestamp") for kind, value in messages if kind == "execution_start"]
    ends = [value.get("timestamp") for kind, value in messages if kind in {"execution_success", "execution_error", "execution_interrupted"}]
    cached = {str(node) for kind, value in messages if kind == "execution_cached" for node in value.get("nodes", [])}
    samplers = {str(key) for key, node in graph.items() if "sampler" in node.get("class_type", "").lower() and "select" not in node.get("class_type", "").lower()}
    profiles = [node["inputs"] for node in graph.values() if node.get("class_type") == "PSV_MiniMaxH3SamplingProfile"]
    documents = [node.get("inputs", {}).get("document_json", "") for node in graph.values() if node.get("class_type") == "PSV_MiniMaxH3Director"]
    seeds = [node.get("inputs", {}).get("noise_seed", node.get("inputs", {}).get("seed")) for node in graph.values() if any(key in node.get("inputs", {}) for key in ("noise_seed", "seed"))]
    status = history.get("status", {})
    return {"completed": status.get("completed", False), "status": status.get("status_str"),
            "execution_seconds": (ends[-1] - starts[0]) / 1000 if starts and ends and starts[0] is not None and ends[-1] is not None else None,
            "sampler_cached": bool(samplers & cached), "cached_node_ids": sorted(cached),
            "valid_timing_sample": bool(status.get("completed") and status.get("status_str") == "success" and samplers and not samplers & cached and starts and ends),
            "profiles": profiles, "seeds": seeds,
            "document_sha256": [hashlib.sha256(value.encode()).hexdigest() for value in documents],
            "sampled_device_peak_vram_bytes": {name: max(values) for name, values in samples.items() if values},
            "memory_measurement": "Whole-device VRAM sampled while observing; includes other processes and can miss short peaks. It is not allocator peak VRAM.",
            "outputs": history.get("outputs", {}),
            "quality_review": {key: None for key in ("identity", "motion", "speech", "synchronization", "artifacts")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    from urllib.parse import quote, urlsplit
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This observer only connects to a local ComfyUI runtime")
    if args.output.exists():
        parser.error("Output already exists; choose a new result file")
    def get(path):
        with urllib.request.urlopen(args.url.rstrip("/") + path, timeout=15) as response:
            return json.load(response)
    runtime = get("/system_stats")
    observed_at = time.time()
    deadline = time.monotonic() + args.timeout
    samples = {}
    while time.monotonic() < deadline:
        stats = get("/system_stats")
        for device in stats.get("devices", []):
            if isinstance(device.get("vram_total"), (int, float)) and isinstance(device.get("vram_free"), (int, float)):
                samples.setdefault(str(device.get("index", "")) + ":" + device.get("name", "GPU"), []).append(device["vram_total"] - device["vram_free"])
        item = get("/history/" + quote(args.prompt_id, safe="")).get(args.prompt_id)
        if item:
            result = {"label": args.label, "prompt_id": args.prompt_id, "observed_at": observed_at,
                      "runtime": runtime, **summarize(item, samples)}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(result, output, indent=2)
                output.write("\n")
            print(json.dumps({key: result[key] for key in ("label", "execution_seconds", "valid_timing_sample", "sampler_cached")}))
            return
        time.sleep(0.5)
    raise TimeoutError("The requested run did not finish within the observation window")


if __name__ == "__main__":
    main()
