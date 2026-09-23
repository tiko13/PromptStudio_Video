import json
import unittest
from unittest.mock import patch

from video.assistant_help import shared_help, CATALOG_ROOT
from video.director import director_chat, _parse_director_turn_route
from test_director import director_document


class VideoHelpTests(unittest.TestCase):
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
