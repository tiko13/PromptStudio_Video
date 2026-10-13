# Registry publication

Every push runs integration Quality once. After it succeeds on `main`, Publish
checks out that exact commit and publishes only if its `pyproject.toml` version
has not been uploaded. Later fix pushes retry automatically; an already uploaded
version is skipped. Publish does not run Quality again.

For a manual release, run Quality on `main`. To retry only a failed upload, rerun
its Publish run. Concurrent publish runs are serialized.

See the shared [publication behavior and validation notes](https://github.com/tiko13/ComfyUI_PromptStudio/blob/master/.github/README.md).
Video integration CI also exercises this workflow's embedded registry check
through the companion's `tests/test_registry_publish.py`.
