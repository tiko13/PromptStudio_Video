import copy
import ast
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import torch
from safetensors.torch import save_file

from video.adapter_contract import normalize_adapter_stack
from video.reference_adapters import inspect_adapter, apply_reference_adapters, resolve_asset, adapter_catalog
from video.model_catalog import matches_folder_type
from video.contracts import default_document, normalize_document, PromptDocumentError
from video.compiler import compile_prompt
from video.store import read_project_store, update_project_store
from video.continuation import build_extension_authoring_document, build_continuation_document


class ReferenceAdapterTests(unittest.TestCase):
    def test_reference_loader_enforces_type_and_preserves_legacy_wildcard(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'nodes' / 'minimax_h3_reference_adapters.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if getattr(node, 'name', '') == 'selection')
        namespace = {'normalize_adapter_stack':normalize_adapter_stack, 'matches_folder_type':matches_folder_type}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'selection', 'exec'), namespace)
        select = namespace['selection']
        for name in ('MiniMax3/hero.safetensors', 'minimax3\\hero.safetensors'):
            self.assertEqual(select([self.row(name=name)], adapter_type='MiniMax3')[0]['name'], name)
        with self.assertRaisesRegex(ValueError, 'Type folder'):
            select([self.row(name='Image/hero.safetensors')], adapter_type='MiniMax3')
        self.assertEqual(select([self.row(name='root.safetensors')])[0]['name'], 'root.safetensors')

    def test_catalog_filters_all_categories_by_shared_type_before_inspection(self):
        names = ['MiniMax3/hero.safetensors', 'minimax3\\nested\\hero.safetensors', 'Image/style.safetensors', 'root.safetensors', 'MiniMax3/_hidden.safetensors']
        folders = types.SimpleNamespace(get_filename_list=lambda category:names, get_full_path_or_raise=lambda category,name:self.root / 'mock.safetensors')
        info = {"kind":"refmod", "members":[{"kind":"image","tokens":4}]}
        with patch('video.reference_adapters.register_reference_folders'), patch.dict('sys.modules', {'folder_paths':folders}), patch('video.reference_adapters.inspect_adapter', return_value=info) as inspect:
            result = adapter_catalog('MiniMax3')
        self.assertEqual(len(result['adapters']), 6)
        self.assertEqual(inspect.call_count, 6)
        self.assertEqual({row['name'] for row in result['adapters']}, set(names[:2]))
        self.assertEqual({row['category'] for row in result['adapters']}, {'loras','refmods','audio_refmods'})
        self.assertTrue(matches_folder_type('root.safetensors', '*'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def asset(self, kind="refmod", audio=False, version=5):
        member = {"kind": "audio" if audio else "image", "_format_version": 4}
        refs = {"latent" if version != 5 else "ref_0": torch.ones((1,32,2,4) if audio else (1,24,1,4,4))}
        metadata = {"refmod_meta": json.dumps(member if version != 5 else {"kind":"bundle", "_format_version":5, "members":[member]})}
        if version != 5: metadata["refmod_meta"] = json.dumps({**member, "_format_version":version})
        if kind == "reflora":
            refs["lora_unet_test.lora_down.weight"] = torch.ones(2,2)
            metadata["h3_hybrid"] = json.dumps({"version":1,"lora":{"keys":1},"refmod_count":1})
        path = self.root / f"{kind}-{version}-{audio}.safetensors"
        save_file(refs, str(path), metadata)
        return path

    def row(self, kind="refmod", **fields):
        return {"name":"folder\\hero.safetensors", "kind":kind, "category":"loras" if kind == "reflora" else "refmods", **fields}

    def apply(self, path, rows, mode="ref2va", budget=16384):
        positive = [["embedding", {"minimax_refs":[{"existing":True}], "minimax_keyframes":["keep"]}]]
        with patch("video.reference_adapters.resolve_asset", return_value=(path.name, path)):
            result = apply_reference_adapters("model", positive, mode, rows, budget)
        return result, positive

    def test_published_standalone_and_bundle_shapes(self):
        for version in (2,4,5):
            for audio in (False,True):
                with self.subTest(version=version,audio=audio):
                    info = inspect_adapter(self.asset(version=version,audio=audio))
                    self.assertEqual(info["kind"], "refmod")
                    self.assertEqual(info["members"][0]["tokens"], 8 if audio else 4)

    def test_reflora_weights_and_references_apply_once_without_mutation(self):
        path = self.asset("reflora")
        calls = []
        sd = types.ModuleType("comfy.sd")
        sd.load_lora_for_models = lambda *args: (calls.append(args) or "patched", None)
        comfy = types.ModuleType("comfy"); comfy.sd = sd
        with patch.dict("sys.modules", {"comfy":comfy, "comfy.sd":sd}):
            result, original = self.apply(path, [self.row("reflora", lora_strength=.6, visual_strength=.5)])
        self.assertEqual(result[0], "patched")
        self.assertEqual(len(calls),1)
        self.assertEqual(list(calls[0][2]), ["lora_unet_test.lora_down.weight"])
        self.assertEqual(calls[0][3],.6)
        self.assertEqual(len(result[1][0][1]["minimax_refs"]),2)
        self.assertEqual(len(original[0][1]["minimax_refs"]),1)
        self.assertEqual(result[1][0][1]["minimax_keyframes"],["keep"])

    def test_zero_strength_bypass_and_reference_only(self):
        path = self.asset("reflora")
        result, original = self.apply(path,[self.row("reflora",lora_strength=0,visual_strength=0,audio_strength=0)],mode="t2va")
        self.assertIs(result[1],original)
        result, _ = self.apply(path,[self.row("reflora",lora_strength=0)])
        self.assertEqual(result[0],"model")
        self.assertEqual(len(result[1][0][1]["minimax_refs"]),2)

    def test_mode_token_budget_and_component_mismatch_fail(self):
        path = self.asset()
        for mode,budget,row,message in [("t2va",16384,self.row(),"REF2VA"),
                ("ref2va",1,self.row(),"budget"),("ref2va",16384,self.row(components="audio"),"not present")]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError,message):
                self.apply(path,[row],mode,budget)

    def test_invalid_layout_and_damaged_hybrid_rejected(self):
        path = self.root / "invalid.safetensors"
        save_file({"latent":torch.ones(1,24,1,3,4)},str(path),{"refmod_meta":json.dumps({"kind":"image","_format_version":4})})
        with self.assertRaisesRegex(ValueError,"Invalid image"): inspect_adapter(path)
        save_file({"ref_0":torch.ones(1,24,1,4,4)},str(path),{
            "refmod_meta":json.dumps({"kind":"bundle","_format_version":5,"members":[{"kind":"image"}]}),
            "h3_hybrid":json.dumps({"version":1,"lora":{"keys":600},"refmod_count":1})})
        with self.assertRaisesRegex(ValueError,"Damaged"): inspect_adapter(path)

    def test_safe_paths_finite_strengths_and_exact_registered_name(self):
        for name in ("../bad", "folder\\..\\bad", "/absolute", "C:\\foreign\\file"):
            with self.assertRaises(ValueError): normalize_adapter_stack([{ "name":name }])
        for value in (float("nan"),float("inf"),101,True):
            with self.assertRaises(ValueError): normalize_adapter_stack([{ "name":"ok", "strength":value }])
        canonical = "folder\\hero.safetensors" # explicit foreign persisted syntax
        native_path = self.root / "folder" / "hero.safetensors"
        folders = types.SimpleNamespace(get_filename_list=lambda category:[canonical], get_full_path_or_raise=lambda category,name:native_path)
        with patch("video.reference_adapters.register_reference_folders"), patch.dict("sys.modules", {"folder_paths":folders}):
            name,path = resolve_asset("refmods","folder/hero.safetensors")
        self.assertEqual(name,canonical)
        self.assertEqual(path,native_path)

    def test_project_roundtrip_mode_and_compilation_do_not_invent_reference_tags(self):
        document = default_document()
        document["content_loras"] = [{"name":"MiniMax3/style.safetensors","strength":.7}]
        document["reference_adapters"] = [self.row()]
        document = normalize_document(document)
        self.assertEqual(document["resolved_mode"],"ref2va")
        prompt = compile_prompt(document)
        self.assertNotIn("hero.safetensors",prompt)
        self.assertNotIn("<Picture 1>",prompt)
        with self.assertRaisesRegex(PromptDocumentError,"RefMods"):
            normalize_document({**document,"mode":"t2va"})
        path = self.root / "projects.json"
        data = update_project_store(str(path),{"version":1,"revision":0,"active_project_id":"test", "projects":[{
            "id":"test","name":"Adapters","document":document,"generations":[]}]})
        self.assertEqual(read_project_store(str(path))["projects"][0]["document"],data["projects"][0]["document"])
        self.assertEqual(data["projects"][0]["document"]["reference_adapters"],document["reference_adapters"])

    def test_extensions_keep_adapters_and_reference_mode(self):
        parent = default_document(); parent["reference_adapters"] = [self.row()]
        parent["content_loras"] = [{"name":"style.safetensors","strength":.5}]
        authored = build_extension_authoring_document(parent,"walks onward",5)
        self.assertEqual(authored["resolved_mode"],"ref2va")
        self.assertEqual(authored["content_loras"],parent["content_loras"])

    def test_director_uses_unnumbered_prose_guide_and_preserves_choices(self):
        from video.director import build_provider_messages
        document = default_document(); document["reference_adapters"] = [self.row()]
        messages, usage = build_provider_messages({"document":document,"scope":"project", "messages":[{"role":"user","content":"Have the character wave."}]})
        self.assertEqual(usage["prompt_guides"],["base-t2va"])
        self.assertIn("unnumbered saved RefMod latents",messages[0]["content"])

    def test_mixed_bundle_filters_modalities_before_tensor_loading(self):
        path = self.root / "mixed.safetensors"
        save_file({"ref_0":torch.ones(1,24,1,4,4),"ref_1":torch.ones(1,32,2,4)},str(path),
                  {"refmod_meta":json.dumps({"kind":"bundle","_format_version":5,"members":[{"kind":"image"},{"kind":"audio"}]})})
        for components,kind in (("visual","image"),("audio","audio")):
            result,_ = self.apply(path,[self.row(components=components)])
            blocks=result[1][0][1]["minimax_refs"]
            self.assertEqual(len(blocks),2)
            self.assertEqual(blocks[-1]["kind"],kind)


if __name__ == "__main__": unittest.main()
