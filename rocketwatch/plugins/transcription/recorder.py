import contextlib
import io
import logging
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("rocketwatch.transcription.recorder")

# Discord audio: 48kHz, 16-bit signed, stereo
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # bytes (16-bit)
FRAME_SIZE = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # bytes per second


class UserStream:
    """Buffers PCM audio for a single user, spilling to disk for long recordings."""

    def __init__(self, user_id: int, tmp_dir: Path) -> None:
        self.user_id = user_id
        self._path = tmp_dir / f"user_{user_id}.pcm"
        self._file = open(self._path, "wb")  # noqa: SIM115
        self._first_timestamp: float | None = None
        self._total_bytes = 0

    def write(self, pcm: bytes, timestamp: float) -> None:
        if self._first_timestamp is None:
            self._first_timestamp = timestamp
        self._file.write(pcm)
        self._total_bytes += len(pcm)

    @property
    def offset(self) -> float:
        """Seconds from recording start to first audio from this user."""
        return self._first_timestamp or 0.0

    @property
    def duration_bytes(self) -> int:
        return self._total_bytes

    def read_all(self) -> bytes:
        self._file.close()
        return self._path.read_bytes()

    def close(self) -> None:
        self._file.close()


class CallRecorder:
    """Records per-user PCM audio from a Discord voice channel."""

    def __init__(self, start_time: float | None = None) -> None:
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="rw_voice_"))
        self._streams: dict[int, UserStream] = {}
        self._start_time = start_time or time.monotonic()
        self._recording = True

    def on_audio(self, user_id: int, pcm: bytes) -> None:
        if not self._recording:
            return
        timestamp = time.monotonic() - self._start_time
        if user_id not in self._streams:
            self._streams[user_id] = UserStream(user_id, self._tmp_dir)
        self._streams[user_id].write(pcm, timestamp)

    def stop(self) -> None:
        self._recording = False

    @property
    def speaker_count(self) -> int:
        return len(self._streams)

    def mix_to_wav(self) -> bytes:
        """Mix all user streams into a single WAV file. Returns WAV bytes."""
        if not self._streams:
            return b""

        # Determine total duration from the longest stream
        max_samples = 0
        user_data: list[tuple[int, np.ndarray[..., np.dtype[np.int16]]]] = []

        for stream in self._streams.values():
            raw = stream.read_all()
            if not raw:
                continue
            samples = np.frombuffer(raw, dtype=np.int16)
            offset_samples = int(stream.offset * SAMPLE_RATE * CHANNELS)
            total = offset_samples + len(samples)
            if total > max_samples:
                max_samples = total
            user_data.append((offset_samples, samples))

        if not user_data:
            return b""

        # Mix into a single int32 buffer to avoid clipping, then normalize
        mixed = np.zeros(max_samples, dtype=np.int32)
        for offset, samples in user_data:
            mixed[offset : offset + len(samples)] += samples.astype(np.int32)

        # Clip to int16 range
        np.clip(mixed, -32768, 32767, out=mixed)
        pcm_out = mixed.astype(np.int16).tobytes()

        # Write WAV
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_out)

        return buf.getvalue()

    def cleanup(self) -> None:
        """Remove temporary files."""
        for stream in self._streams.values():
            with contextlib.suppress(Exception):
                stream.close()
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)
