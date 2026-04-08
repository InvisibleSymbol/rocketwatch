from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import davey
from discord import Member, VoiceChannel, VoiceClient, VoiceState
from discord.abc import Messageable
from discord.ext.commands import Cog
from discord.ext.voice_recv import BasicSink, VoiceRecvClient
from pydub import AudioSegment

from rocketwatch.bot import RocketWatch

if TYPE_CHECKING:
    from discord import User
    from discord.ext.voice_recv.opus import VoiceData
from rocketwatch.plugins.transcription.pipeline import TranscriptionPipeline
from rocketwatch.plugins.transcription.recorder import CallRecorder
from rocketwatch.utils.config import cfg
from rocketwatch.utils.embeds import Embed
from rocketwatch.utils.file import TextFile
from rocketwatch.utils.llm import create_provider

TRANSCRIPTIONS_DIR = Path("../transcriptions")

log = logging.getLogger("rocketwatch.transcription")
logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)


class Transcription(Cog):
    def __init__(self, bot: RocketWatch) -> None:
        self.bot = bot
        self._config = cfg.transcription
        self._recorder: CallRecorder | None = None
        self._voice_client: VoiceClient | None = None
        self._artifact_dir: Path | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None

        llm = create_provider(self._config.llm)
        if llm and self._config.stt.provider:
            self._pipeline: TranscriptionPipeline | None = TranscriptionPipeline(
                stt_config=self._config.stt,
                llm_provider=llm,
            )
        else:
            self._pipeline = None
            log.warning("Transcription plugin loaded but LLM/STT not configured")

    def _count_voice_users(self, channel: VoiceChannel) -> int:
        """Count non-bot members in a voice channel."""
        return sum(1 for m in channel.members if not m.bot)

    @Cog.listener()
    async def on_voice_state_update(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        if member.bot or not self._config.voice_channel_id or not self._pipeline:
            return

        target_id = self._config.voice_channel_id

        # Someone joined the target channel
        if after.channel and after.channel.id == target_id:
            assert isinstance(after.channel, VoiceChannel)
            self._cancel_grace()
            count = self._count_voice_users(after.channel)
            if count >= self._config.min_users and not self._recorder:
                await self._start_recording(after.channel)

        # Someone left the target channel
        if before.channel and before.channel.id == target_id:
            assert isinstance(before.channel, VoiceChannel)
            count = self._count_voice_users(before.channel)
            if count == 0 and self._recorder:
                await self._stop_and_process()
            elif count < self._config.min_users and self._recorder:
                self._start_grace()

    def _start_grace(self) -> None:
        """Start the grace period before auto-disconnecting."""
        self._cancel_grace()
        self._grace_task = asyncio.create_task(self._grace_countdown())

    def _cancel_grace(self) -> None:
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
            self._grace_task = None

    async def _grace_countdown(self) -> None:
        try:
            await asyncio.sleep(self._config.leave_grace_seconds)
            await self._stop_and_process()
        except asyncio.CancelledError:
            pass

    async def _start_recording(self, channel: VoiceChannel) -> None:
        log.info(f"Auto-joining voice channel: {channel.name}")
        try:
            vc = await channel.connect(cls=VoiceRecvClient)
        except Exception:
            log.exception("Failed to connect to voice channel")
            return

        self._voice_client = vc
        self._recorder = CallRecorder(self._get_artifact_dir() / "raw")

        def sink_callback(user: User | None, data: VoiceData) -> None:
            if not user or not self._recorder:
                return

            opus_data = data.packet.decrypted_data
            if not opus_data:
                return

            # Decrypt DAVE (E2E encryption) layer
            dave_session = vc._connection.dave_session
            if dave_session and not dave_session.can_passthrough(user.id):
                try:
                    opus_data = dave_session.decrypt(
                        user.id, davey.MediaType.audio, opus_data
                    )
                except Exception:
                    return

            self._recorder.on_opus(user.id, opus_data)

        vc.listen(BasicSink(sink_callback, decode=False))

        # Start max-duration timeout
        self._timeout_task = asyncio.create_task(self._recording_timeout())
        log.info("Recording started")

    async def _recording_timeout(self) -> None:
        try:
            await asyncio.sleep(self._config.max_recording_minutes * 60)
            log.warning("Max recording duration reached, stopping")
            await self._stop_and_process()
        except asyncio.CancelledError:
            pass

    async def _stop_and_process(self) -> None:
        current = asyncio.current_task()
        if (
            self._grace_task
            and self._grace_task is not current
            and not self._grace_task.done()
        ):
            self._grace_task.cancel()
        self._grace_task = None
        if (
            self._timeout_task
            and self._timeout_task is not current
            and not self._timeout_task.done()
        ):
            self._timeout_task.cancel()
        self._timeout_task = None

        recorder = self._recorder
        vc = self._voice_client
        self._recorder = None
        self._voice_client = None

        if not recorder:
            return

        recorder.stop()

        # Disconnect from voice
        if vc and vc.is_connected():
            await vc.disconnect()

        if not self._pipeline:
            recorder.cleanup()
            return

        # Check if there was enough audio to process
        if recorder.speaker_count == 0:
            log.info("No speakers detected, discarding recording")
            recorder.cleanup()
            return

        try:
            user_segments = recorder.get_user_segments()
            if not user_segments:
                log.info("Empty recording, discarding")
                return

            self._save_audio(user_segments)

            # Resolve user IDs to display names
            usernames: dict[int, str] = {}
            guild_id = cfg.discord.owner.server_id
            for user_id in user_segments:
                if member := await self.bot.get_or_fetch_member(guild_id, user_id):
                    usernames[user_id] = member.display_name
                else:
                    usernames[user_id] = f"User {user_id}"

            transcript = await self._pipeline.transcribe_users(user_segments, usernames)
            self._save_transcript(transcript)

            summary = await self._pipeline.summarize(transcript)
            if not summary:
                log.info("No substantive content, discarding")
                return

            await self._post_results(transcript, summary)
        except Exception as e:
            await self.bot.report_error(e)
        finally:
            recorder.cleanup()
            self._artifact_dir = None

    def _get_artifact_dir(self) -> Path:
        """Get or create the artifact directory for the current recording."""
        if self._artifact_dir is None:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M")
            self._artifact_dir = TRANSCRIPTIONS_DIR / timestamp
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        return self._artifact_dir

    def _save_audio(
        self,
        user_segments: dict[int, list[tuple[float, Path]]],
    ) -> None:
        """Mix per-user WAV files into a single MP3."""
        out_dir = self._get_artifact_dir()

        mixed: AudioSegment | None = None
        for _uid, segments in user_segments.items():
            for offset, wav_path in segments:
                track = AudioSegment.from_wav(str(wav_path))
                if mixed is None:
                    mixed = AudioSegment.silent(duration=int(offset * 1000)) + track
                else:
                    end_ms = int(offset * 1000) + len(track)
                    if end_ms > len(mixed):
                        mixed += AudioSegment.silent(duration=end_ms - len(mixed))
                    mixed = mixed.overlay(track, position=int(offset * 1000))

        if mixed:
            mixed.export(out_dir / "audio.mp3", format="mp3")
            log.info(f"Audio saved to {out_dir}")

    def _save_transcript(self, transcript: str) -> None:
        """Save transcript to disk."""
        out_dir = self._get_artifact_dir()
        (out_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
        log.info(f"Transcript saved to {out_dir}")

    async def _post_results(self, transcript: str, summary: str) -> None:
        channel_id = self._config.output_channel_id
        if not channel_id:
            log.warning("No output channel configured")
            return

        channel = await self.bot.get_or_fetch_channel(channel_id)
        assert isinstance(channel, Messageable)

        embed = Embed(title="Voice Call Summary", description=summary[:4096])

        transcript_file = TextFile(transcript, "transcript.txt")
        await channel.send(embed=embed, file=transcript_file)
        log.info("Transcript and summary posted")

    async def cog_unload(self) -> None:
        self._cancel_grace()
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        if self._voice_client and self._voice_client.is_connected():
            await self._voice_client.disconnect()
        if self._recorder:
            self._recorder.stop()
            self._recorder.cleanup()


async def setup(bot: RocketWatch) -> None:
    await bot.add_cog(Transcription(bot))
