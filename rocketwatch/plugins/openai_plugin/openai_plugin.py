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
        self.last_summary_dict = {}
        self.last_financial_advice_dict = {}

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
        if message.reference and not isinstance(message.reference.resolved, DeletedReferencedMessage) and message.reference.resolved:
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
        # ratelimit
        if self.last_summary_dict.get(ctx.channel.id) is not None and (datetime.now(timezone.utc) - self.last_summary_dict.get(ctx.channel.id)) < timedelta(minutes=15):
            await ctx.send("You can only summarize once every 15 minutes.", ephemeral=True)
            return
        if ctx.channel.id not in [405163713063288832, 998627604686979214]:
            await ctx.send("You can't summarize here.", ephemeral=True)
            return
        last_ts = self.last_summary_dict.get(ctx.channel.id) or datetime(2021, 1, 1, tzinfo=timezone.utc)
        response, prompt = await self.prompt_model(ctx.channel, "The following is a summarization of the above chat log:", last_ts)
        e = Embed()
        e.title = "Chat Summarization"
        e.description = response["choices"][0]["text"]
        e.set_footer(text=f"Request cost: ${response['usage']['total_tokens'] / 1000 * 0.02:.2f} | /donate if you like this command")
        # attach the prompt as a file
        f = BytesIO(prompt.encode("utf-8"))
        f.name = "prompt.txt"
        f = File(f, filename=f"prompt_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
        # send message in the channel
        await ctx.send("done")
        await ctx.channel.send(embed=e, file=f)
        self.last_summary_dict[ctx.channel.id] = datetime.now(timezone.utc)

    # a function that generates the prompt for the model by taking an array of messages, a prefix and a suffix
    def generate_prompt(self, messages, prefix, suffix):
        messages.sort(key=lambda x: x.created_at)
        prompt = "\n".join([self.message_to_text(message) for message in messages]).replace("\n\n", "\n")
        return f"{prefix}\n\n{prompt}\n\n{suffix}"

    async def prompt_model(self, channel, prompt, cut_off_ts):
        messages = [message async for message in channel.history(limit=512) if message.content != ""]
        messages = [message for message in messages if message.author.id != self.bot.user.id]
        messages = [message for message in messages if message.created_at > cut_off_ts]
        if len(messages) < 32:
            return None
        prefix = "The following is a chat log. Everything prefixed with `>` is a quote."
        while len(self.tokenizer(self.generate_prompt(messages, prefix, prompt))['input_ids']) > (4096 - 256):
            # remove the oldest message
            messages.pop(0)

        prompt = self.generate_prompt(messages, prefix, prompt)
        response = openai.Completion.create(
            engine=self.engine,
            prompt=prompt,
            max_tokens=256,
            temperature=0.0,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0
        )
        return response, prompt

    """
    func financial_advice(), which does the same thing as summarize_chat, but with a different prompt, asking it to give financial advice
    should only work for channel 998627604686979214
    """
    @hybrid_command()
    async def financial_advice(self, ctx: Context):
        await ctx.defer(ephemeral=True)
        # ratelimit every hour
        if self.last_financial_advice_dict.get(ctx.channel.id) is not None and (datetime.now(timezone.utc) - self.last_financial_advice_dict.get(ctx.channel.id)) < timedelta(hours=1):
            await ctx.send("You can only get financial advice once every hour.", ephemeral=True)
            return
        if ctx.channel.id != 998627604686979214:
            await ctx.send("You can't use this command here.", ephemeral=True)
            return

        last_ts = self.last_financial_advice_dict.get(ctx.channel.id) or datetime(2021, 1, 1, tzinfo=timezone.utc)
        response, prompt = await self.prompt_model(ctx.channel, "The following is financial advice based on the above chat log:", last_ts)
        e = Embed()
        e.title = "Financial Advice"
        e.description = response['choices'][0]['t   ext']
        e.set_footer(text=f"Request cost: ${response['usage']['total_tokens'] / 1000 * 0.02:.2f} | /donate if you like this command")
        # attach the prompt as a file
        f = BytesIO(prompt.encode("utf-8"))
        f.name = "prompt.txt"
        f = File(f, filename=f"prompt_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
        await ctx.send("done")
        await ctx.channel.send(embed=e, file=f)
        self.last_financial_advice_dict[ctx.channel.id] = datetime.now(timezone.utc)


async def setup(self):
    await self.add_cog(OpenAi(self))
