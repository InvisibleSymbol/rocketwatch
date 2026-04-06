import io
import logging
import math

from openai import AsyncOpenAI

from rocketwatch.utils.config import STTConfig
from rocketwatch.utils.llm import LLMProvider

log = logging.getLogger("rocketwatch.transcription.pipeline")

SUMMARIZE_SYSTEM_PROMPT = """\
You are summarizing a community call transcript for a cryptocurrency Discord server \
(Rocket Pool). Produce a structured summary with:

- **Key Topics**: Main subjects discussed
- **Decisions Made**: Any conclusions or agreements reached
- **Action Items**: Tasks assigned or next steps mentioned
- **Notable Points**: Important statements or insights

Keep the summary concise but comprehensive. Use bullet points."""


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

    async def transcribe_audio(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio via the Whisper API, chunking if needed."""
        from pydub import AudioSegment

        audio = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        duration_seconds = len(audio) / 1000.0
        chunk_ms = self._chunk_seconds * 1000
        num_chunks = max(1, math.ceil(duration_seconds / self._chunk_seconds))

        log.info(
            f"Transcribing {duration_seconds:.0f}s of audio in {num_chunks} chunk(s)"
        )

        client = AsyncOpenAI(
            api_key=self._stt.api_key,
            base_url=self._stt.base_url or None,
        )

        segments: list[str] = []
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
                response_format="verbose_json",
            )

            text = response.text  # type: ignore[union-attr]
            if text:
                timestamp = f"[{start_ms // 1000 // 60}:{start_ms // 1000 % 60:02d}]"
                segments.append(f"{timestamp} {text}")

            log.debug(f"Chunk {i + 1}/{num_chunks} transcribed")

        return "\n\n".join(segments)

    async def summarize(self, transcript: str) -> str:
        """Summarize a transcript using the configured LLM."""
        result = await self._llm.complete(
            SUMMARIZE_SYSTEM_PROMPT,
            f"Summarize this community call transcript:\n\n{transcript}",
            max_tokens=2048,
        )
        return result.strip()

    async def process(self, wav_bytes: bytes) -> tuple[str, str]:
        """Full pipeline: transcribe then summarize. Returns (transcript, summary)."""
        transcript = await self.transcribe_audio(wav_bytes)
        summary = await self.summarize(transcript)
        return transcript, summary
