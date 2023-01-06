import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO

import openai
from discord import Object, File, DeletedReferencedMessage
from discord.app_commands import guilds
from discord.ext import commands
from discord.ext.commands import Context, is_owner
from discord.ext.commands import hybrid_command
from transformers import GPT2TokenizerFast

from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("openai")


class OpenAi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        openai.api_key = cfg["openai.secret"]
        # log all possible engines
        engines = openai.Engine.list()
        log.debug(engines)
        self.engine = "text-davinci-003"
        self.tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")
        self.last_summary = None

    @classmethod
    def message_to_text(cls, message):
        text = f"{message.author.name} — at {message.created_at.strftime('%H:%M')}\n {message.content}"
        # if there is an image attached, add it to the text as a note
        metadata = []
        if message.attachments:
            metadata.append(f"{len(message.attachments)} attachments")
        if message.embeds:
            metadata.append(f"{len(message.embeds)} embeds")
        # replies and make sure the reference is not deleted
        if message.reference and not isinstance(message.reference.resolved, DeletedReferencedMessage):
            # show name of referenced message author
            # and the first 10 characters of the referenced message
            metadata.append(f"reply to \"{message.reference.resolved.content[:10]}…\" from {message.reference.resolved.author.name}")
        if metadata:
            text += f" <{', '.join(metadata)}>\n"
        # replace all <@[0-9]+> with the name of the user
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", f"@{mention.name}")
        return text

    @hybrid_command()
    async def summarize_chat(self, ctx: Context):
        await ctx.defer(ephemeral=True)
        if self.last_summary is not None and (datetime.now(timezone.utc) - self.last_summary) < timedelta(minutes=15):
            await ctx.send("You can only summarize once every 15 minutes.", ephemeral=True)
            return
        if ctx.channel.id != 405163713063288832:
            await ctx.send("You can't summarize here.", ephemeral=True)
            return
        messages = [message async for message in ctx.channel.history(limit=512) if message.content != ""]
        messages = [message for message in messages if (datetime.now(timezone.utc) - message.created_at) < timedelta(hours=1)]
        # if last_summary is set, cut off the messages at that point as well
        if self.last_summary is not None:
            messages = [message for message in messages if message.created_at > self.last_summary]
        messages = [message for message in messages if message.author.id != self.bot.user.id]
        if len(messages) < 32:
            await ctx.send("Not enough messages to summarize.", ephemeral=True)
            return
        while len(self.tokenizer("".join(self.message_to_text(message)) for message in messages)['input_ids']) > (4000 - 300):
            messages.pop()
        self.last_summary = datetime.now(timezone.utc)
        messages.sort(key=lambda x: x.created_at)
        prompt = "\n".join([self.message_to_text(message) for message in messages]).replace("\n\n", "\n")
        response = openai.Completion.create(
            engine=self.engine,
            prompt=f"The following is a chat log. anything text prefixed with > is a quote.\n\n{prompt}\n\nThe following is a summary of this chat log:",
            max_tokens=256,
            temperature=0.7,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=1
        )
        e = Embed()
        e.title = f"Chat Summarization of the last {len(messages)} messages"
        e.description = response["choices"][0]["text"]
        e.set_footer(text=f"Request cost: ${response['usage']['total_tokens'] / 1000 * 0.02:.2f} | /donate if you like this command")
        f = BytesIO(prompt.encode("utf-8"))
        f.name = "prompt.txt"
        f = File(f, filename=f"prompt_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
        # send message in the channel
        await ctx.send("done")
        await ctx.channel.send(file=f, embed=e)


async def setup(bot):
    await bot.add_cog(OpenAi(bot))
