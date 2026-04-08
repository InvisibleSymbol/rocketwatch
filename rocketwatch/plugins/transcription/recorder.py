import contextlib
import json
import logging
import time
import wave
from pathlib import Path

from discord.opus import Decoder as OpusDecoder

log = logging.getLogger("rocketwatch.transcription.recorder")

# Discord audio: 48kHz, 16-bit signed, stereo
SAMPLE_RATE = OpusDecoder.SAMPLING_RATE  # 48000
CHANNELS = OpusDecoder.CHANNELS  # 2
SAMPLE_WIDTH = OpusDecoder.SAMPLE_SIZE // OpusDecoder.CHANNELS  # 2 bytes (16-bit)
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH

MIN_SEGMENT_DURATION = 1.0  # seconds
MIN_SEGMENT_BYTES = int(MIN_SEGMENT_DURATION * BYTES_PER_SECOND) + 44  # +WAV header


class UserStream:
    """Buffers PCM audio for a single user, writing WAV files to disk."""

    def __init__(self, user_id: int, out_dir: Path) -> None:
        self.user_id = user_id
        self._out_dir = out_dir
        self._segment_index = 0
        self._wav: wave.Wave_write | None = None
        self._segment_start: float = 0.0
        self._last_timestamp: float = 0.0
        self._segments: list[tuple[float, Path]] = []  # (offset, path)

    def _start_segment(self, timestamp: float) -> None:
        self._close_wav()
        path = self._out_dir / f"user_{self.user_id}_{self._segment_index}.wav"
        self._wav = wave.open(str(path), "wb")  # noqa: SIM115
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(SAMPLE_WIDTH)
        self._wav.setframerate(SAMPLE_RATE)
        self._segment_start = timestamp
        self._last_timestamp = timestamp
        self._segments.append((timestamp, path))
        self._segment_index += 1

    def write(self, pcm: bytes, timestamp: float) -> None:
        if not self._wav:
            self._start_segment(timestamp)
        else:
            # Start a new segment if there's a significant gap (>5s)
            expected = self._last_timestamp + len(pcm) / (
                SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
            )
            if timestamp - expected > 5.0:
                self._start_segment(timestamp)

        self._last_timestamp = timestamp
        assert self._wav is not None
        self._wav.writeframes(pcm)

    def get_segments(self) -> list[tuple[float, Path]]:
        """Return list of (offset_seconds, wav_path) for each segment."""
        self._close_wav()
        return list(self._segments)

    def _close_wav(self) -> None:
        if self._wav:
            self._wav.close()
            self._wav = None

    def close(self) -> None:
        self._close_wav()


class CallRecorder:
    """Records per-user PCM audio from a Discord voice channel.

    Receives raw Opus packets and decodes them to PCM internally,
    bypassing discord-ext-voice-recv's broken Opus decoder.
    """

    def __init__(self, out_dir: Path, start_time: float | None = None) -> None:
        self._out_dir = out_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
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
            self._streams[user_id] = UserStream(user_id, self._out_dir)

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

    def get_user_segments(self) -> dict[int, list[tuple[float, Path]]]:
        """Return per-user WAV file paths with their start offsets.

        Also writes a manifest.json alongside the WAV files so the
        segment timestamps can be recovered from disk.
        """
        result: dict[int, list[tuple[float, Path]]] = {}
        manifest: dict[str, list[dict[str, str | float]]] = {}
        for user_id, stream in self._streams.items():
            segments = [
                (offset, path)
                for offset, path in stream.get_segments()
                if path.stat().st_size >= MIN_SEGMENT_BYTES
            ]
            if segments:
                result[user_id] = segments
                manifest[str(user_id)] = [
                    {"offset": offset, "file": path.name} for offset, path in segments
                ]

        if manifest:
            manifest_path = self._out_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))

        return result

    def cleanup(self) -> None:
        """Close open file handles."""
        for stream in self._streams.values():
            with contextlib.suppress(Exception):
                stream.close()
