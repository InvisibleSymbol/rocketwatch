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

    def get_user_wavs(self) -> dict[int, tuple[float, bytes]]:
        """Return per-user WAV files with their start offsets.

        Returns a dict of user_id -> (offset_seconds, wav_bytes).
        """
        result: dict[int, tuple[float, bytes]] = {}
        for user_id, stream in self._streams.items():
            raw = stream.read_all()
            if raw:
                result[user_id] = (stream.offset, self._pcm_to_wav(raw))
        return result

    def cleanup(self) -> None:
        """Remove temporary files."""
        for stream in self._streams.values():
            with contextlib.suppress(Exception):
                stream.close()
        import shutil

        shutil.rmtree(self._tmp_dir, ignore_errors=True)
