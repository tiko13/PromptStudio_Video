import unittest
from tools.observe_h3_benchmark import summarize


class BenchmarkTests(unittest.TestCase):
    def history(self, cached):
        return {"prompt": [0, "id", {"1": {"class_type": "SamplerCustomAdvanced", "inputs": {}}}],
                "status": {"completed": True, "status_str": "success", "messages": [
                    ["execution_start", {"timestamp": 1000}], ["execution_cached", {"nodes": cached}],
                    ["execution_success", {"timestamp": 4000}]]}}

    def test_cached_sampler_cannot_claim_speedup(self):
        result = summarize(self.history(["1"]), {"gpu": [100, 200, 150]})
        self.assertFalse(result["valid_timing_sample"])
        self.assertEqual(result["execution_seconds"], 3)
        self.assertEqual(result["sampled_device_peak_vram_bytes"], {"gpu": 200})

    def test_uncached_success_and_failed_run_are_distinguished(self):
        history = self.history([])
        self.assertTrue(summarize(history, {})["valid_timing_sample"])
        history["status"]["status_str"] = "error"
        self.assertFalse(summarize(history, {})["valid_timing_sample"])
