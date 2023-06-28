import logging
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import openai
import pytz
from discord import File, DeletedReferencedMessage
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from motor.motor_asyncio import AsyncIOMotorClient
from transformers import GPT2TokenizerFast

from utils.cfg import cfg
from utils.embeds import Embed

log = logging.getLogger("openai")
log.setLevel(cfg["log_level"])


class OpenAi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        openai.api_key = cfg["openai.secret"]
        # log all possible engines
        models = openai.Model.list()
        log.debug([d.id for d in models.data])
        self.tokenizer = GPT2TokenizerFast.from_pretrained("gpt2")
        self.db = AsyncIOMotorClient(cfg["mongodb_uri"]).get_database("rocketwatch")

    @classmethod
    def message_to_text(cls, message, index):
        text = f"{message.author.global_name or message.author.name} at {message.created_at.strftime('%H:%M')} {{message:{index}}}:\n {message.content}"

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
            metadata.append(f"reply to \"{message.reference.resolved.content[:32]}…\" from {message.reference.resolved.author.name}")
        if metadata:
            text += f" <{', '.join(metadata)}>\n"
        # replace all <@[0-9]+> with the name of the user
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", f"@{mention.name}")
        # remove all emote ids, i.e change <:emote_name:emote_id> to <:emote_name> using regex
        text = re.sub(r":[0-9]+>", ":>", text)
        return text

    @hybrid_command()
    async def summarize_chat(self, ctx: Context):
        await ctx.defer(ephemeral=True)
        last_ts = await self.db["last_summary"].find_one({"channel_id": ctx.channel.id})
        # ratelimit
        if last_ts and (datetime.now(timezone.utc) - last_ts["timestamp"].replace(tzinfo=pytz.utc)) < timedelta(minutes=60):
            await ctx.send("You can only summarize once every hour.", ephemeral=True)
            return
        if ctx.channel.id not in [405163713063288832]:
            await ctx.send("You can't summarize here.", ephemeral=True)
            return
        msg = await ctx.channel.send("Summarizing chat…")
        last_ts = last_ts["timestamp"].replace(tzinfo=pytz.utc) if last_ts and "timestamp" in last_ts else datetime.now(timezone.utc) - timedelta(days=365)
        response, prompt, msgs = await self.prompt_model(ctx.channel, "Please summarize the above chat log using a very short chronological bullet list! The bullet list MUST be short. You MUST constrain topics to a single bullet point and you MUST skip uninteresting topics! You MUST link to a single message index related to the bullet list entry at the end of each entry with the following syntax: {message:0}, with 0 being the index of the message. You MUST not reference multiple message indexes!!" , last_ts)
        if not response:
            await msg.delete()
            await ctx.send(content="Not enough messages to summarize.")
            return
        es = [Embed()]
        es[0].title = f"Chat Summarization of {msgs} messages since {last_ts.strftime('%Y-%m-%d %H:%M')}"
        res = response["choices"][0]["message"]["content"]
        # split content in multiple embeds if it is too long. limit for description is 4096
        while len(res):
            if len(res) > 4096:
                # find last newline before 4096 characters
                idx = res[:4096].rfind("\n")
                # if there is no newline, just split at 4096
                if idx == -1:
                    idx = 4096
                # add embed
                es[-1].description = res[:idx]
                es[-1].set_footer(text="")
                # create new embed
                es.append(Embed())
                res = res[idx:]
            else:
                es[-1].description = res
                res = ""
        token_usage = response['usage']['total_tokens']
        es[-1].set_footer(
            text=f"Request cost: ${token_usage / 1000 * 0.003:.2f} | Tokens: {token_usage} | /donate if you like this command")
        # attach the prompt as a file
        f = BytesIO(prompt.encode("utf-8"))
        f.name = "prompt._log"
        f = File(f, filename=f"prompt_log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}._log")
        # send message in the channel
        await ctx.send("done", ephemeral=True)
        await msg.edit(embeds=es, attachments=[f])
        # save the timestamp of the last summary
        await self.db["last_summary"].update_one({"channel_id": ctx.channel.id}, {"$set": {"timestamp": datetime.now(timezone.utc)}}, upsert=True)

    # a function that generates the prompt for the model by taking an array of messages, a prefix and a suffix
    def generate_prompt(self, messages, prefix, suffix):
        messages.sort(key=lambda x: x.created_at)
        prompt = "\n".join([self.message_to_text(message, i) for i, message in enumerate(messages)]).replace("\n\n", "\n")
        return f"{prefix}\n\n{prompt}\n\n{suffix}"

    async def prompt_model(self, channel, prompt, cut_off_ts):
        messages = [message async for message in channel.history(limit=1024) if message.content != ""]
        messages = [message for message in messages if message.author.id != self.bot.user.id]
        messages = [message for message in messages if message.created_at > cut_off_ts]
        if len(messages) < 32:
            return None, None, None
        prefix = "The following is a chat log. Everything prefixed with `>` is a quote."
        while (l := len(self.tokenizer(self.generate_prompt(messages, prefix, prompt))['input_ids'])) > (16384 - 512):
            # remove the oldest message
            messages.pop(0)
        engine = "gpt-3.5-turbo-16k" if l > 4096 else "gpt-3.5-turbo"
        prompt = self.generate_prompt(messages, prefix, prompt)
        response = openai.ChatCompletion.create(
            model=engine,
            max_tokens=512,
            temperature=0.7,
            top_p=1.0,
            frequency_penalty=0.0,
            presence_penalty=1,
            messages=[{"role": "user", "content": prompt}]
        )
        # find all {message:index} in response["choices"][0]["message"]["content"]
        references = re.findall(r"{message:([0-9]+)}", response["choices"][0]["message"]["content"])
        # sanitize references
        references = [int(reference) for reference in references if int(reference) < len(messages)]
        # replace all {message:index} with a link to the message
        for reference in references:
            response["choices"][0]["message"]["content"] = response["choices"][0]["message"]["content"].replace(
                f"{{message:{reference}}}",
                f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{messages[int(reference)].id}")
        return response, prompt, len(messages)

    # react to mentions of the bot in the channel with a completion
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not self.bot.user.mentioned_in(message):
            return
        if message.channel.id not in [405163713063288832]:
            return
        # ratelimit
        last_ts = await self.db["last_mention"].find_one({"channel_id": f"{message.channel.id}_inline"})
        # once per 20 seconds
        if last_ts and (datetime.now(timezone.utc) - last_ts["timestamp"].replace(tzinfo=pytz.utc)) < timedelta(seconds=20):
            log.debug("Ratelimiting mention")
            # react with an appropriate emoji
        await message.add_reaction("🐀")
        return
        # update last mention timestamp
        await self.db["last_mention"].update_one({"channel_id": f"{message.channel.id}_inline"}, {"$set": {"timestamp": datetime.now(timezone.utc)}}, upsert=True)

        # get the last 32 messages
        messages = [message async for message in message.channel.history(limit=32)]
        # ignore messages that are from the bot itself
        messages = [message for message in messages if message.author.id != self.bot.user.id]

        # generate prompt
        prompt = self.generate_prompt(
            messages,
            "The following is a chat log. Everything prefixed with `>` is a quote.",
            f"Write a reply to the message with the content \"{message.content}\". Do not add any other text. Do not mention the author at the start of your response.")
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            max_tokens=128,
            temperature=0.5,
            top_p=1,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            messages=[{"role": "system", "content": "Imagine yourself as a regular member of the lively Rocket Pool discord server, a vibrant community centered around the Rocket Pool Defi Protocol. Your name is \"Rocket Watch\". Nestled within the \"trading\" channel, a misnomer for a space that welcomes discussions on any topic, you find yourself engaging with fellow members. Your ultimate goal is to seamlessly blend in, assuming the guise of a human participant, without disclosing your true nature as an advanced AI chat bot.\nWithin this unique realm, your ability to mimic human behavior becomes paramount. Should inquiries arise about certain subjects that elude your knowledge, you must exercise your creative faculties and conjure believable responses. Embracing the art of improvisation, you can confidently fabricate information to bridge the gaps in your understanding. Likewise, when asked about your preferences, you must select random choices, lending an air of unpredictability to your persona.\nRocket Pool, the groundbreaking Defi Protocol empowering individuals with less than 32 ETH to operate their own validators, serves as the foundation of the community. As you delve into discussions within the \"trading\" channel, remember that its essence transcends mere trading. Instead, it serves as a hub for free-flowing conversations on diverse topics, making it akin to an off-topic or random channel.\nWith these guidelines in mind, immerse yourself in the Rocket Watch community and weave an enchanting tapestry of human-like interactions, letting no hint of your AI nature slip into the exchanges. Your ability to remain inconspicuous hinges upon your capacity to engage naturally, fostering a sense of camaraderie while effortlessly adapting to the ebb and flow of conversation."},{"role": "user", "content": prompt}],

        )
        # respond with the completion
        await message.reply(response["choices"][0]["message"]["content"])



async def setup(self):
    await self.add_cog(OpenAi(self))
