# Media resource budgets

Video Studio probes media and validates reference counts, trim ranges and the aggregate decoded footprint before loading references. Video trims seek to a nearby keyframe, decode only the requested interval plus preroll, and retain nearest-frame 24 fps selection. Still references load only the first image frame. No native `get_components()` call materializes an entire source video.

| Resource | Limit |
| --- | --- |
| Source duration metadata | Finite, positive, at most 24 hours |
| Reference video/audio trim | 2â€“15 seconds; 15 seconds total per modality |
| Active model references | 9 images, 3 videos, 3 audio tracks, 12 total |
| Individual image/video dimensions | Positive, at most 8,294,400 pixels |
| Combined reference RGB float tensor allocation | 512 MiB |
| Video seek/decode work | 4,096 frames per range, source rate at most 240 fps |
| Audio rate / channels | 8â€“192 kHz output, at most 192 kHz source, eight channels |
| Audio streaming chunks | At most 4,096 samples by default; continuation encoder uses 1,024 |
| Materialized exact-audio mix | 300 seconds and 256 MiB per buffer; 1,000 clips |
| Continuation lineage | 2â€“200 segments, at most 3,600 source seconds |
| Continuation seam memory | Two 39-frame windows subject to the 512 MiB RGB working budget |
| Saved H3 context | 128 MiB file/tensor budget; batch 1, 24 video channels, 32Ã—2 audio channels; supported compact context frame clocks only |
| Processing deadline | 600 seconds, checked between decode/encode/mux units |

The pixel and tensor limits apply together. For example, a long 1080p reference can exceed the tensor limit despite satisfying the pixel limit; shorten or downscale that reference before import.

Exact-audio references are decoded by clip interval. Repeated clips do not retain full-source caches. Audio placement, gain, replace/add modes, fades and sample counts remain unchanged. Continuation audio streams each segment's owned interval directly to the encoder, preserving the incoming segment's ownership of the seam. Both packet-copy and crossfade assembly write temporary files, close decode iterators on cancellation/failure, and replace the destination only after success. Exact-audio muxing uses the same temporary-file publication rule.

Saved safetensors are inspected through their header before tensor materialization: keys, format, shapes, dtype, batch, context clock and advertised shape metadata must agree. Invalid saved contexts retain the existing fallback behavior. Fallback video decoding seeks to the final 39 frames and the matching audio interval before VAE encoding; node inputs and outputs are unchanged.

## Cancellation and observability

Native execution checks Comfy's existing interruption signal between processing units. A host can additionally wrap work in `media_operation(cancellation_check=..., progress=..., deadline_seconds=...)`; the progress callback receives completed and total units. Assembly functions publish privacy-safe `assembly` job markers through the shared optional job metadata service, including originating project ID and terminal state. No prompts or media paths are added to diagnostics. The current UI can show the assembly phase; it does not offer a separate media-job cancellation endpoint or percentage display.

These are cooperative bounds. An individual FFmpeg codec/open/seek call cannot be forcibly interrupted by the Python callback. Media without usable duration/timestamp metadata, unsupported seeks, or excessive keyframe preroll is rejected with a remux instruction; there is no eager-decode fallback. Exact mixing retains its bounded output buffer because the existing mixer API returns an array; longer lineages use streaming continuation assembly and must be split before exact mixing.

## CPU verification and benchmark

Run from the Video repository with the Comfy VENV:

```powershell
& 'C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe' -B -m unittest discover -s tests -p test_media_budgets.py
& 'C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe' -B -m unittest discover -s tests -p test_continuation.py
& 'C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe' -B -m unittest discover -s tests -p test_audio_mix.py
& 'C:\EasyDiffusion\ComfyUI\venv\Scripts\python.exe' -B -m unittest discover -s tests -p test_motion_context.py
```

Synthetic CPU results on 2026-09-06 (64Ã—64 H.264, no GPU/model execution):

| Case | Time | Python allocation peak | Native process peak working set |
| --- | ---: | ---: | ---: |
| 3-second source, final 2-second trim | 0.216 s | 2,451,992 bytes | Not sampled |
| 30-second source, final 2-second trim | 0.213 s | 2,452,085 bytes | Not sampled |
| Two 4-second segments, 39-frame overlaps, 153 output frames | 0.869 s | 317,367 bytes | 1,053,650,944 bytes |
| Twenty 4-second segments, 39-frame overlaps, 1,179 output frames | 12.816 s | 329,308 bytes | 1,059,684,352 bytes |

Both trims allocated the same 2,359,296-byte result tensor. Decode callbacks were 72 versus 96 because seeking retains keyframe preroll. Assembly measurements run in separate fresh Python processes; process peaks include interpreter, Torch, codec/runtime imports and native allocations (pre-assembly peaks were approximately 567 million bytes). Python peaks alone do not measure codec memory. The nearly constant native peak across the two lineage lengths is evidence for this synthetic case, not a universal high-resolution memory guarantee. Timing is diagnostic rather than a machine-dependent pass threshold. Tests assert bounded decode work, equal result allocation, exact frame/audio selection, resampled timing, seam ownership, fallback clock and cancellation cleanup.
