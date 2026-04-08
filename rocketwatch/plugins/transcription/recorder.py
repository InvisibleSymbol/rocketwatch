import contextlib
import io
import logging
import tempfile
import time
import wave
from pathlib import Path

from discord.opus import Decoder as OpusDecoder

log = logging.getLogger("rocketwatch.transcription.recorder")

# Discord audio: 48kHz, 16-bit signed, stereo
SAMPLE_RATE = OpusDecoder.SAMPLING_RATE  # 48000
CHANNELS = OpusDecoder.CHANNELS  # 2
SAMPLE_WIDTH = OpusDecoder.SAMPLE_SIZE // OpusDecoder.CHANNELS  # 2 bytes (16-bit)


class UserStream:
    """Buffers PCM audio for a single user, spilling to disk for long recordings."""

    def __init__(self, user_id: int, tmp_dir: Path) -> None:
        self.user_id = user_id
        self._tmp_dir = tmp_dir
        self._segment_index = 0
        self._file: io.BufferedWriter | None = None
        self._segment_start: float = 0.0
        self._last_timestamp: float = 0.0
        self._segments: list[tuple[float, Path]] = []  # (offset, path)

    def _start_segment(self, timestamp: float) -> None:
        if self._file:
            self._file.close()
        path = self._tmp_dir / f"user_{self.user_id}_{self._segment_index}.pcm"
        self._file = open(path, "wb")  # noqa: SIM115
        self._segment_start = timestamp
        self._last_timestamp = timestamp
        self._segments.append((timestamp, path))
        self._segment_index += 1

    def write(self, pcm: bytes, timestamp: float) -> None:
        if not self._file:
            self._start_segment(timestamp)
        else:
            # Start a new segment if there's a significant gap (>5s)
            expected = self._last_timestamp + len(pcm) / (
                SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
            )
            if timestamp - expected > 5.0:
                self._start_segment(timestamp)

        self._last_timestamp = timestamp
        assert self._file is not None
        self._file.write(pcm)

    def get_segments(self) -> list[tuple[float, bytes]]:
        """Return list of (offset_seconds, pcm_bytes) for each segment."""
        if self._file:
            self._file.close()
            self._file = None
        return [(offset, path.read_bytes()) for offset, path in self._segments]

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


class CallRecorder:
    """Records per-user PCM audio from a Discord voice channel.

    Receives raw Opus packets and decodes them to PCM internally,
    bypassing discord-ext-voice-recv's broken Opus decoder.
    """

    def __init__(self, start_time: float | None = None) -> None:
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="rw_voice_"))
        self._streams: dict[int, UserStream] = {}
        self._decoders: dict[int, OpusDecoder] = {}
        self._start_time = start_time or time.monotonic()
        self._recording = True

    def on_opus(self, user_id: int, opus_data: bytes) -> None:
        """Receive an Opus packet, decode to PCM, and buffer it."""
        if not self._recording or not opus_data:
            return

        if user_id not in self._decoders:
            log.info(f"Receiving audio from user {user_id}")
            self._decoders[user_id] = OpusDecoder()
            self._streams[user_id] = UserStream(user_id, self._tmp_dir)

        try:
            pcm = self._decoders[user_id].decode(opus_data, fec=False)
        except Exception:
            log.debug(
                f"Failed to decode Opus for user {user_id}: "
                f"len={len(opus_data)} first_bytes={opus_data[:16].hex()}"
            )
            return

        timestamp = time.monotonic() - self._start_time
        self._streams[user_id].write(pcm, timestamp)

    def stop(self) -> None:
        self._recording = False

    @property
    def speaker_count(self) -> int:
        return len(self._streams)

    def _pcm_to_wav(self, pcm: bytes) -> bytes:
        """Convert raw PCM bytes to WAV format."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        return buf.getvalue()

    def get_user_segments(self) -> dict[int, list[tuple[float, bytes]]]:
        """Return per-user WAV segments with their start offsets.

        Returns a dict of user_id -> [(offset_seconds, wav_bytes), ...].
        """
        result: dict[int, list[tuple[float, bytes]]] = {}
        for user_id, stream in self._streams.items():
            segments = stream.get_segments()
            wavs = [(offset, self._pcm_to_wav(pcm)) for offset, pcm in segments if pcm]
            if wavs:
                result[user_id] = wavs
        return result

    def cleanup(self) -> None:
        """Remove temporary files."""
        for stream in self._streams.values():
            with contextlib.suppress(Exception):
                stream.close()
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)
