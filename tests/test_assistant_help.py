import json
import ast
import itertools
import unittest
from unittest.mock import patch

from video.assistant_help import shared_help, CATALOG_ROOT
from video.director import director_chat, _parse_director_turn_route
from test_director import director_document


class VideoHelpTests(unittest.TestCase):
    def test_every_companion_node_has_inventory_and_selectable_details(self):
        help = shared_help()
        cards = help.load_catalog((CATALOG_ROOT,))
        facts = help.normalize_facts({}, studio="video")
        inventory = "\n".join(help.render_packet(cards, facts, ["nodes", "video-nodes"])["instructions"])
        index = {item["topic"]: item["summary"] for item in help.topic_index(cards, facts)}
        names = {}
        for path in (CATALOG_ROOT.parent / "nodes").glob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                    and any(isinstance(target, ast.Name) and target.id == "NODE_DISPLAY_NAME_MAPPINGS"
                            for target in node.targets)):
                    names.update({ast.literal_eval(key): ast.literal_eval(value)
                                  for key, value in zip(node.value.keys, node.value.values) if key is not None})
        self.assertTrue(names)
        for node_id, display_name in names.items():
            with self.subTest(node=node_id):
                self.assertIn(node_id, inventory)
                self.assertIn(display_name, inventory)
                details = [card for card in help.applicable_cards(cards, facts)
                           if card["topic"] != "video-nodes" and node_id in card["body"]]
                self.assertTrue(details, f"No details for {node_id}")
                self.assertTrue(any(node_id in index[card["topic"]] for card in details))
        self.assertIn("PromptStudioInput", inventory)

    def test_all_video_topic_combinations_fit_shared_budget(self):
        help = shared_help()
        cards = help.load_catalog((CATALOG_ROOT,))
        facts = help.normalize_facts({}, studio="video")
        topics = [item["topic"] for item in help.topic_index(cards, facts)]
        for selected in itertools.combinations(topics, help.MAX_TOPICS):
            with self.subTest(topics=selected):
                help.render_packet(cards, facts, list(selected))

    def test_node_followups_and_multistage_help_do_not_compile_or_propose(self):
        turn = {"route": "discuss", "help_domain": "app", "confidence": 1}
        cases = [
            ("List all nodes in both product packages.", ["nodes", "video-nodes"],
             {"nodes", "video.nodes"}),
            ("How do I wire its outputs?", ["video-node-turbo"], {"video.node-turbo"}),
            ("How do I add or change custom inputs in a multi-step workflow?",
             ["workflow-inputs", "video-stages"], {"workflow-inputs", "video.stages"}),
        ]
        cases.append(("How do I ripple a cut and loop the result without stretching my audio?",
                      ["video-timing", "video-shot-timing", "video-playback"],
                      {"video.timing", "video.shot-timing", "video.playback"}))
        for question, topics, documents in cases:
            request = {"document": director_document(), "scope": "project", "messages": [
                {"role": "assistant", "content": "The Turbo Profile applies the acceleration LoRA."},
                {"role": "user", "content": question}]}
            with self.subTest(question=question), patch("video.director._classify_director_turn", return_value=turn), \
                    patch("video.director.build_provider_messages") as build, \
                    patch("video.director.generate_chat", side_effect=[json.dumps({"topics": topics}),
                        '{"message":"Node help.","proposal":{"bad":"ignored"}}']) as generate:
                result = director_chat(request)
                build.assert_not_called()
                self.assertIsNone(result["proposal"])
                self.assertEqual(set(result["help_documents"]), documents)
                self.assertIn("Turbo Profile", json.dumps(generate.call_args_list[0].args))

    def test_video_catalog_uses_shared_resolver_without_image_instructions(self):
        help = shared_help()
        cards = help.load_catalog((CATALOG_ROOT,))
        facts = help.normalize_facts({}, studio="video")
        packet = help.render_packet(cards, facts, ["references", "workflows", "director"])
        self.assertEqual(set(packet["documents"]), {"video.references", "video.workflows", "video.director"})
        self.assertNotIn("references.qwen", packet["documents"])

    def test_app_help_skips_vision_and_production_compilation(self):
        turn = {"route": "discuss", "help_domain": "app", "confidence": 1}
        request = {"document": director_document(), "scope": "project", "attachments": [{"invalid":"unused for help"}],
                   "messages": [{"role": "user", "content": "How do I add references?"}], "help_context": {"media_limit": 12}}
        with patch("video.director._classify_director_turn", return_value=turn), patch("video.director.load_vision_images") as vision, patch("video.director.build_provider_messages") as build, patch("video.director.generate_chat", side_effect=[
            '{"topics":["references"]}', '{"message":"Use Add in Media.","proposal":{"bad":"ignored"}}'
        ]) as generate:
            result = director_chat(request)
        vision.assert_not_called()
        build.assert_not_called()
        self.assertIsNone(result["proposal"])
        self.assertEqual(result["help_documents"], ["video.references"])
        self.assertIn("active model media limit: 12", generate.call_args.args[1][0]["content"])

    def test_creative_discussion_does_not_retrieve_help(self):
        turn = {"route": "discuss", "help_domain": "prompting", "confidence": 1}
        with patch("video.director._classify_director_turn", return_value=turn), patch("video.director._director_help_packet") as retrieve, patch("video.director.generate_chat", return_value='{"message":"Describe the motion concretely.","proposal":null}'):
            result = director_chat({"document": director_document(), "scope": "project", "messages": [{"role":"user","content":"How can I describe a slow turn?"}]})
        retrieve.assert_not_called()
        self.assertIsNone(result["proposal"])

    def test_help_domain_is_preserved_and_validated(self):
        raw = {"route":"discuss", "confidence":1, "help_domain":"both", "app_help_query":"Where is Media?"}
        self.assertEqual(_parse_director_turn_route(json.dumps(raw))["help_domain"], "both")
        with self.assertRaises(ValueError):
            _parse_director_turn_route(json.dumps({**raw,"help_domain":"invented"}))
