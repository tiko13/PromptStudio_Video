"""Opt-in real-LLM help checks; no project storage, queue, or server changes."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "ComfyUI_PromptStudio" / "tests"))
from test_regressions import load_modules
from video import director


def run(url):
    with tempfile.TemporaryDirectory() as directory:
        _, routes = load_modules(directory)
        settings = {"llm_provider":"llamacpp", "llamacpp_url":url, "thinking_mode":"Disabled", "max_response_tokens":900}
        def generate(data, messages, images):
            assert not images
            system = "\n".join(item["content"] for item in messages if item["role"] == "system")
            request = {**settings, **data, "messages":[{"role":item["role"],"text":item["content"]} for item in messages if item["role"] != "system"]}
            return routes._consult(request, system, allow_partial=False, response_schema=data.get("_response_schema"))
        with patch.object(director, "generate_chat", side_effect=generate):
            for question, domain in [("How do I make a shirt with tiny sleeves?","prompting"), ("How do I change model?","app"), ("What wording describes tiny sleeves, and how do I add media?","both")]:
                request = {**settings,"scope":"project","messages":[{"role":"user","content":question}]}
                result = director._classify_director_turn(request)
                assert (result["route"],result["help_domain"]) == ("discuss",domain), result
                print(json.dumps({"question":question,"route":result["route"],"domain":result["help_domain"]}),flush=True)
            answer = director.director_chat({**settings,"scope":"shot","continuation_context":{},
                "help_context":{"workflow":"Source workflow"},"document":{"mode":"auto"},
                "messages":[{"role":"user","content":"How do I add references to this structured extension?"}]})
            assert answer["proposal"] is None and "video.references" in answer["help_documents"], answer
            print(json.dumps(answer,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--url",default="http://127.0.0.1:8080")
    run(parser.parse_args().url)
