"""CPU media limits and seekable, bounded decode primitives (no model work)."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import time

MAX_PIXELS = 3840 * 2160
MAX_TENSOR_BYTES = 512 * 1024 * 1024
MAX_SOURCE_SECONDS = 24 * 3600
MAX_ASSEMBLY_SECONDS = 3600
MAX_AUDIO_SECONDS = 300
MAX_SAMPLE_RATE = 192000
MAX_DECODE_FRAMES = 4096
MAX_OPERATION_SECONDS = 600
_operation = ContextVar("promptstudio_media_operation", default=None)


@contextmanager
def media_operation(cancellation_check=None, progress=None, deadline_seconds=MAX_OPERATION_SECONDS):
    """Optional host hooks; native node inputs/outputs stay unchanged."""
    token = _operation.set((time.monotonic() + deadline_seconds, cancellation_check, progress))
    try:
        yield
    finally:
        _operation.reset(token)


def checkpoint(completed=None, total=None):
    state = _operation.get()
    if state:
        deadline, cancel, progress = state
        if time.monotonic() >= deadline:
            raise TimeoutError("Media processing exceeded its time budget")
        if cancel and cancel():
            raise InterruptedError("Media processing was cancelled")
        if progress and completed is not None:
            progress(completed, total)
    # Comfy supplies interruption for native execution, without a new node input.
    try:
        import comfy.model_management as management
    except ImportError:
        return
    management.throw_exception_if_processing_interrupted()


def duration(value, maximum=MAX_SOURCE_SECONDS):
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"Media duration must be finite and between 0 and {maximum:g} seconds")
    return value


def geometry(width, height, frames=1):
    width, height, frames = int(width), int(height), int(frames)
    if min(width, height, frames) <= 0 or width * height > MAX_PIXELS:
        raise ValueError("Media dimensions exceed the 3840x2160 pixel budget or are empty")
    if width * height * frames * 3 * 4 > MAX_TENSOR_BYTES:
        raise ValueError("Decoded media exceeds the 512 MiB tensor budget; reduce resolution or trim length")


def stream_duration(container, stream):
    import av
    if stream.duration is not None and stream.time_base is not None:
        return duration(float(stream.duration * stream.time_base))
    if container.duration is not None:
        return duration(container.duration / av.time_base)
    if stream.type == "video" and stream.frames and stream.average_rate:
        return duration(stream.frames / float(stream.average_rate))
    raise ValueError("Media has no usable duration metadata; remux it before importing")


def trim_range(total, start=0, end=None, maximum=15, minimum=0):
    start = float(start or 0)
    end = total if end is None else min(float(end), total)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("Media trim range must be finite, non-negative and non-empty")
    duration(end - start, maximum)
    if end - start < minimum:
        raise ValueError(f"Media trim must be at least {minimum:g} seconds")
    return start, end


def _seek(container, stream, start):
    if start <= 0:
        return
    origin = float((stream.start_time or 0) * stream.time_base)
    try:
        # Retain codec/resampler preroll; output is cropped by timestamp below.
        container.seek(int((origin + max(0, start - 1)) / float(stream.time_base)),
                       stream=stream, backward=True)
    except (ValueError, OSError) as exc:
        raise ValueError("Media cannot seek to this trim; remux it before importing") from exc


def iter_audio(path, sample_rate=48000, layout="stereo", start=0, end=None, chunk_size=4096):
    """Yield a precise padded interval using bounded chunks, retaining no source history."""
    import av
    import numpy as np
    sample_rate = int(sample_rate)
    if not 8000 <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError("Media sample rate is outside the 8-192 kHz budget")
    if not 1 <= int(chunk_size) <= 65536:
        raise ValueError("Invalid media chunk size")
    channels = len(av.AudioLayout(layout).channels)
    if channels > 8:
        raise ValueError("Media exceeds the eight-channel budget")
    with av.open(path, mode="r") as container:
        stream = container.streams.audio[0] if container.streams.audio else None
        if end is None:
            if stream is None:
                return
            end = stream_duration(container, stream)
        start, end = trim_range(MAX_SOURCE_SECONDS, start, end, MAX_ASSEMBLY_SECONDS)
        wanted = round((end - start) * sample_rate)
        emitted = 0
        if stream is not None:
            stream_duration(container, stream)
            if int(stream.codec_context.sample_rate or 0) > MAX_SAMPLE_RATE or int(stream.codec_context.channels or 0) > 8:
                raise ValueError("Source audio sample rate exceeds the budget")
            _seek(container, stream, start)
            origin = float((stream.start_time or 0) * stream.time_base)
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=sample_rate)
            fallback_position = 0
            decoded_samples = 0
            done = False
            for frame in container.decode(stream):
                checkpoint()
                decoded_samples += frame.samples
                if decoded_samples > (end - start + 5) * max(int(frame.sample_rate), sample_rate):
                    raise ValueError("Audio seek did not reach the requested range within its decode budget")
                for converted in resampler.resample(frame) or []:
                    position = (round((float(converted.pts * converted.time_base) - origin - start) * sample_rate)
                                if converted.pts is not None else fallback_position - round(start * sample_rate))
                    value = converted.to_ndarray()
                    fallback_position += value.shape[-1]
                    # Ignore preroll and encoder padding; preserve actual gaps as silence.
                    while emitted < min(wanted, max(0, position)):
                        count = min(chunk_size, wanted - emitted, position - emitted)
                        yield np.zeros((channels, count), dtype=np.float32)
                        emitted += count
                    offset = max(0, emitted - position)
                    while offset < value.shape[-1] and emitted < wanted:
                        count = min(chunk_size, value.shape[-1] - offset, wanted - emitted)
                        yield np.ascontiguousarray(value[:, offset:offset + count], dtype=np.float32)
                        emitted += count
                        offset += count
                    if emitted >= wanted:
                        done = True
                        break
                if done:
                    break
            if not done:
                for converted in resampler.resample(None) or []:
                    value = converted.to_ndarray()
                    for offset in range(0, value.shape[-1], chunk_size):
                        count = min(chunk_size, value.shape[-1] - offset, wanted - emitted)
                        if count <= 0:
                            break
                        yield np.ascontiguousarray(value[:, offset:offset + count], dtype=np.float32)
                        emitted += count
        while emitted < wanted:
            checkpoint()
            count = min(chunk_size, wanted - emitted)
            yield np.zeros((channels, count), dtype=np.float32)
            emitted += count


def audio_array(path, sample_rate=48000, start=0, end=None, layout="stereo"):
    import av
    import numpy as np
    if end is None:
        with av.open(path) as container:
            if not container.streams.audio:
                return np.zeros((len(av.AudioLayout(layout).channels), 0), dtype=np.float32)
            end = stream_duration(container, container.streams.audio[0])
    start, end = trim_range(MAX_SOURCE_SECONDS, start, end, MAX_AUDIO_SECONDS)
    sample_rate = int(sample_rate)
    if not 8000 <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError("Media sample rate is outside the 8-192 kHz budget")
    samples = round((end - start) * sample_rate)
    channels = len(av.AudioLayout(layout).channels)
    if channels > 8 or samples * channels * 4 > MAX_TENSOR_BYTES // 2:
        raise ValueError("Decoded audio exceeds the 256 MiB buffer budget")
    result = np.empty((channels, samples), dtype=np.float32)
    position = 0
    for value in iter_audio(path, sample_rate, layout, start, end):
        result[:, position:position + value.shape[-1]] = value
        position += value.shape[-1]
    return result


def video_range(path, start=0, end=None, fps=24, minimum=0):
    """Decode nearest CFR reference frames into one bounded Comfy IMAGE tensor."""
    import av
    import numpy as np
    import torch
    with av.open(path) as container:
        if not container.streams.video:
            raise ValueError("Media contains no video stream")
        stream = container.streams.video[0]
        total = stream_duration(container, stream)
        start, end = trim_range(total, start, end, 15, minimum)
        rate = float(stream.average_rate or 0)
        if not math.isfinite(rate) or not 0 < rate <= 240:
            raise ValueError("Video frame rate is outside the 0-240 fps budget")
        count = int(math.floor((end - start) * fps + 1e-8))
        geometry(stream.width, stream.height, count)
        result = np.empty((count, stream.height, stream.width, 3), dtype=np.float32)
        targets = np.rint((start + np.arange(count) / fps) * rate) / rate
        _seek(container, stream, max(0, targets[0] - 1 / rate))
        origin = float((stream.start_time or 0) * stream.time_base)
        previous = None
        previous_time = None
        position = 0
        for decoded, frame in enumerate(container.decode(stream)):
            checkpoint(position, count)
            if decoded >= MAX_DECODE_FRAMES:
                raise ValueError("Video exceeds the bounded seek/decode frame budget")
            if (frame.width, frame.height) != (stream.width, stream.height):
                raise ValueError("Video changes dimensions mid-stream")
            if frame.pts is None:
                raise ValueError("Video lacks timestamps required for bounded trim decoding")
            stamp = float(frame.pts * frame.time_base) - origin
            while position < count and targets[position] <= stamp + 1e-8:
                selected = (previous if previous is not None and
                            abs(targets[position] - previous_time) < abs(stamp - targets[position]) else frame)
                result[position] = selected.to_ndarray(format="rgb24") / np.float32(255)
                position += 1
            if position >= count:
                break
            previous, previous_time = frame, stamp
        if position != count:
            # Last CFR sample may round to the final frame, matching native clamp.
            if previous is None or targets[-1] - previous_time > 1 / rate + 1e-6:
                raise ValueError("Video ended before its declared trim range")
            while position < count:
                result[position] = previous.to_ndarray(format="rgb24") / np.float32(255)
                position += 1
        return torch.from_numpy(result), start, end


def bounded_media(function):
    from functools import wraps
    @wraps(function)
    def run(*args, **kwargs):
        if _operation.get() is not None:
            return function(*args, **kwargs)
        with media_operation():
            return function(*args, **kwargs)
    return run


def assembly_job(function):
    """Publish optional shared job markers without changing Comfy node sockets."""
    from functools import wraps
    from uuid import uuid4
    @wraps(function)
    def run(*args, **kwargs):
        ledger = None
        job_id = 'assembly-' + uuid4().hex
        try:
            from .llm_provider import job_ledger
            ledger = job_ledger()
            project_id = kwargs.get('project_id', args[-2] if len(args) >= 2 else None)
            ledger.start(job_id, studio='video', kind='assembly', data={'origin': {'project_id': project_id}})
            ledger.update(job_id, studio='video', phase='assembly')
        except Exception:
            ledger = None  # Metadata service is optional in standalone node tests.
        try:
            result = bounded_media(function)(*args, **kwargs)
        except BaseException as exc:
            if ledger:
                cancelled = isinstance(exc, InterruptedError) or type(exc).__name__ == 'InterruptProcessingException'
                ledger.update(job_id, studio='video', state='cancelled' if cancelled else 'failed', error=exc)
            raise
        if ledger:
            ledger.update(job_id, studio='video', state='complete')
        return result
    return run


video_range = bounded_media(video_range)
audio_array = bounded_media(audio_array)
