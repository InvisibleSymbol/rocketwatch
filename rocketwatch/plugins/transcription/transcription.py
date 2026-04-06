import asyncio
import io
import logging

import discord
from discord import (
    Attachment,
    Interaction,
    Member,
    VoiceChannel,
    VoiceState,
)
from discord.abc import Messageable
from discord.app_commands import command, describe, guilds
from discord.ext.commands import Cog
from pydub import AudioSegment

from rocketwatch.bot import RocketWatch
from rocketwatch.plugins.transcription.pipeline import TranscriptionPipeline
from rocketwatch.plugins.transcription.recorder import CallRecorder
from rocketwatch.utils.config import cfg
from rocketwatch.utils.file import TextFile
from rocketwatch.utils.llm import create_provider

log = logging.getLogger("rocketwatch.transcription")


class Transcription(Cog):
    def __init__(self, bot: RocketWatch) -> None:
        self.bot = bot
        self._config = cfg.transcription
        self._recorder: CallRecorder | None = None
        self._voice_client: discord.VoiceClient | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._timeout_task: asyncio.Task[None] | None = None

        llm = create_provider(self._config.llm)
        if llm and self._config.stt.provider:
            self._pipeline: TranscriptionPipeline | None = TranscriptionPipeline(
                stt_config=self._config.stt,
                llm_provider=llm,
                chunk_duration_seconds=self._config.chunk_duration_seconds,
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
            if count < self._config.min_users and self._recorder:
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
            from discord.ext.voice_recv import VoiceRecvClient

            vc = await channel.connect(cls=VoiceRecvClient)
        except Exception:
            log.exception("Failed to connect to voice channel")
            return

        self._voice_client = vc
        self._recorder = CallRecorder()

        def sink_callback(
            user: Member | discord.User | None, data: discord.VoiceData
        ) -> None:  # type: ignore[name-defined]
            if user and self._recorder:
                self._recorder.on_audio(user.id, data.pcm)

        vc.listen(discord.ext.voice_recv.BasicSink(sink_callback))  # type: ignore[attr-defined]

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
        self._cancel_grace()
        if self._timeout_task and not self._timeout_task.done():
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
            wav_bytes = recorder.mix_to_wav()
            if not wav_bytes:
                log.info("Empty recording, discarding")
                return

            transcript, summary = await self._pipeline.process(wav_bytes)

            # Discard if transcript is too short
            word_count = len(transcript.split())
            if word_count < self._config.min_transcript_words:
                log.info(f"Transcript too short ({word_count} words), discarding")
                return

            await self._post_results(transcript, summary)
        except Exception:
            log.exception("Failed to process recording")
        finally:
            recorder.cleanup()

    async def _post_results(self, transcript: str, summary: str) -> None:
        channel_id = self._config.output_channel_id
        if not channel_id:
            log.warning("No output channel configured")
            return

        channel = await self.bot.get_or_fetch_channel(channel_id)
        assert isinstance(channel, Messageable)

        embed = discord.Embed(
            title="Community Call Summary",
            description=summary[:4096],
            color=discord.Color.blue(),
        )

        transcript_file = TextFile(transcript, "transcript.txt")
        await channel.send(embed=embed, file=transcript_file)
        log.info("Transcript and summary posted")

    @command(name="transcribe_file", description="Transcribe an uploaded audio file")
    @describe(audio="Audio file to transcribe (mp3, wav, m4a, ogg)")
    @guilds(cfg.discord.owner.server_id)
    async def transcribe_file(
        self, interaction: Interaction, audio: Attachment
    ) -> None:
        if not self._pipeline:
            await interaction.response.send_message(
                "Transcription is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            audio_bytes = await audio.read()

            audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
            wav_buf = io.BytesIO()
            audio_segment.export(wav_buf, format="wav")
            wav_bytes = wav_buf.getvalue()

            transcript, summary = await self._pipeline.process(wav_bytes)

            word_count = len(transcript.split())
            if word_count < self._config.min_transcript_words:
                await interaction.followup.send(
                    f"Transcript too short ({word_count} words), discarding.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="Community Call Summary",
                description=summary[:4096],
                color=discord.Color.blue(),
            )
            transcript_file = TextFile(transcript, "transcript.txt")
            await interaction.followup.send(embed=embed, file=transcript_file)
        except Exception:
            log.exception("Failed to transcribe uploaded file")
            await interaction.followup.send(
                "Failed to transcribe audio file.", ephemeral=True
            )

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
