"""Compare the editing clock against the existing generation execution contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from video.contracts import frame_count_for_duration
from video.continuation import continuation_frame_plan


@unittest.skipUnless(shutil.which("node"), "Node is required for browser/backend clock parity")
class TimelineParityTests(unittest.TestCase):
    def test_half_frame_ties_native_grid_and_continuation_delivery(self):
        normal = [frame / 48 for frame in range(1, 7184)]
        extension = [frame / 48 for frame in range(240, 721)]
        module = (Path(__file__).resolve().parents[1] / "web" / "js" / "timeline-model.js").as_uri()
        script = f"""
          import {{generatedFrames, timingPlan}} from {json.dumps(module)};
          let input = ''; for await (const part of process.stdin) input += part;
          const {{normal, extension}} = JSON.parse(input);
          process.stdout.write(JSON.stringify({{
            normal:normal.map(generatedFrames),
            extension:extension.map(duration_seconds => timingPlan({{
              document:{{duration_seconds}},
              extension_source:{{parent_project_id:'parent',parent_generation_id:'take'}},
            }}).delivered),
          }}));
        """
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                                input=json.dumps({"normal": normal, "extension": extension}),
                                capture_output=True, text=True, check=True, timeout=30)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["normal"], [frame_count_for_duration(value) for value in normal])
        self.assertEqual(actual["extension"], [continuation_frame_plan(value)["delivered_frames"]
                                               for value in extension])
