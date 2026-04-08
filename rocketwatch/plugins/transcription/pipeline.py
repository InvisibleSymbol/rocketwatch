import io
import logging

from openai import AsyncOpenAI
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

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
Omit any section that has no content. Use bullet points and keep the summary concise. \
Give equal attention to all parts of the transcript, regardless of when they occurred."""


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
    ) -> None:
        self._stt = stt_config
        self._llm = llm_provider

    async def _transcribe_wav(
        self, wav_bytes: bytes, client: AsyncOpenAI
    ) -> list[tuple[float, str]]:
        """Transcribe a single WAV stream, splitting on silence gaps.

        Returns list of (offset_seconds, text).
        """
        audio = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        ranges = detect_nonsilent(
            audio, min_silence_len=5000, silence_thresh=audio.dBFS - 16
        )
        if not ranges:
            return []

        results: list[tuple[float, str]] = []
        for i, (start_ms, end_ms) in enumerate(ranges):
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
                results.append((start_ms / 1000.0, text.strip()))

        return results

    async def transcribe_users(
        self,
        user_segments: dict[int, list[tuple[float, bytes]]],
        usernames: dict[int, str],
    ) -> str:
        """Transcribe per-user audio segments and interleave by timestamp.

        Args:
            user_segments: user_id -> [(offset_seconds, wav_bytes), ...]
            usernames: user_id -> display name
        """
        client = AsyncOpenAI(api_key=self._stt.api_key)
        segments: list[_Segment] = []

        for user_id, wav_segments in user_segments.items():
            speaker = usernames.get(user_id, f"User {user_id}")
            log.info(f"Transcribing audio for {speaker} ({len(wav_segments)} segments)")

            for offset, wav_bytes in wav_segments:
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
            lines.append(f"[{minutes}:{seconds:02d}] {seg.speaker}: {seg.text}")

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
        user_segments: dict[int, list[tuple[float, bytes]]],
        usernames: dict[int, str],
    ) -> tuple[str, str]:
        """Full pipeline with speaker labels. Returns (transcript, summary)."""
        transcript = await self.transcribe_users(user_segments, usernames)
        summary = await self.summarize(transcript)
        return transcript, summary

    async def process(self, wav_bytes: bytes) -> tuple[str, str]:
        """Full pipeline without speaker labels. Returns (transcript, summary)."""
        transcript = await self.transcribe_audio(wav_bytes)
        summary = await self.summarize(transcript)
        return transcript, summary
