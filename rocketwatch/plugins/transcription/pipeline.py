import io
import logging
import math

from openai import AsyncOpenAI
from pydub import AudioSegment

from rocketwatch.utils.config import STTConfig
from rocketwatch.utils.llm import LLMProvider

log = logging.getLogger("rocketwatch.transcription.pipeline")

SUMMARIZE_SYSTEM_PROMPT = """\
You are summarizing a Rocket Pool community call transcript for Discord server members \
who missed the call. Produce a structured summary with the following sections:

- **Topics Discussed**: Brief overview of each subject covered
- **Decisions & Outcomes**: Conclusions reached, proposals approved or rejected
- **Action Items**: Tasks assigned or next steps, attributed to specific people if mentioned
- **Open Questions**: Unresolved topics or debates that need follow-up

Attribute key statements to speakers by name when relevant. \
Omit any section that has no content. Use bullet points and keep the summary concise."""


class _Segment:
    """A timestamped piece of transcript from a single speaker."""

    __slots__ = ("speaker", "start", "text")

    def __init__(self, start: float, speaker: str, text: str) -> None:
        self.start = start
        self.speaker = speaker
        self.text = text


class TranscriptionPipeline:
    def __init__(
        self,
        stt_config: STTConfig,
        llm_provider: LLMProvider,
        chunk_duration_seconds: int = 600,
    ) -> None:
        self._stt = stt_config
        self._llm = llm_provider
        self._chunk_seconds = chunk_duration_seconds

    async def _transcribe_wav(
        self, wav_bytes: bytes, client: AsyncOpenAI
    ) -> list[tuple[float, str]]:
        """Transcribe a single WAV stream. Returns list of (offset_seconds, text)."""
        audio = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        duration_seconds = len(audio) / 1000.0
        chunk_ms = self._chunk_seconds * 1000
        num_chunks = max(1, math.ceil(duration_seconds / self._chunk_seconds))

        results: list[tuple[float, str]] = []
        for i in range(num_chunks):
            start_ms = i * chunk_ms
            end_ms = min((i + 1) * chunk_ms, len(audio))
            chunk = audio[start_ms:end_ms]

            buf = io.BytesIO()
            chunk.export(buf, format="mp3")
            buf.seek(0)
            buf.name = f"chunk_{i}.mp3"

            response = await client.audio.transcriptions.create(
                model=self._stt.model,
                file=buf,
                response_format="json",
            )

            text = response.text
            if text and text.strip():
                offset = start_ms / 1000.0
                results.append((offset, text.strip()))

        return results

    async def transcribe_users(
        self,
        user_streams: dict[int, tuple[float, bytes]],
        usernames: dict[int, str],
    ) -> str:
        """Transcribe per-user audio streams and interleave by timestamp.

        Args:
            user_streams: user_id -> (offset_seconds, wav_bytes)
            usernames: user_id -> display name
        """
        client = AsyncOpenAI(api_key=self._stt.api_key)
        segments: list[_Segment] = []

        for user_id, (offset, wav_bytes) in user_streams.items():
            speaker = usernames.get(user_id, f"User {user_id}")
            log.info(f"Transcribing audio for {speaker}")

            chunks = await self._transcribe_wav(wav_bytes, client)
            for chunk_offset, text in chunks:
                segments.append(
                    _Segment(
                        start=offset + chunk_offset,
                        speaker=speaker,
                        text=text,
                    )
                )

        # Sort by timestamp
        segments.sort(key=lambda s: s.start)

        # Format with timestamps and speaker labels
        lines: list[str] = []
        for seg in segments:
            minutes = int(seg.start) // 60
            seconds = int(seg.start) % 60
            lines.append(f"[{minutes}:{seconds:02d}] **{seg.speaker}**: {seg.text}")

        return "\n\n".join(lines)

    async def transcribe_audio(self, wav_bytes: bytes) -> str:
        """Transcribe a single WAV file (no speaker labels). Fallback for uploaded files."""
        client = AsyncOpenAI(api_key=self._stt.api_key)
        chunks = await self._transcribe_wav(wav_bytes, client)

        lines: list[str] = []
        for offset, text in chunks:
            minutes = int(offset) // 60
            seconds = int(offset) % 60
            lines.append(f"[{minutes}:{seconds:02d}] {text}")

        return "\n\n".join(lines)

    async def summarize(self, transcript: str) -> str:
        """Summarize a transcript using the configured LLM."""
        result = await self._llm.complete(
            SUMMARIZE_SYSTEM_PROMPT,
            f"Summarize this community call transcript:\n\n{transcript}",
            max_tokens=2048,
        )
        return result.strip()

    async def process_users(
        self,
        user_streams: dict[int, tuple[float, bytes]],
        usernames: dict[int, str],
    ) -> tuple[str, str]:
        """Full pipeline with speaker labels. Returns (transcript, summary)."""
        transcript = await self.transcribe_users(user_streams, usernames)
        summary = await self.summarize(transcript)
        return transcript, summary

    async def process(self, wav_bytes: bytes) -> tuple[str, str]:
        """Full pipeline without speaker labels. Returns (transcript, summary)."""
        transcript = await self.transcribe_audio(wav_bytes)
        summary = await self.summarize(transcript)
        return transcript, summary
