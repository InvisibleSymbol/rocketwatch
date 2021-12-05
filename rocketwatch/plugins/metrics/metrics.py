import logging
from datetime import datetime, timedelta

import motor.motor_asyncio
from discord import Color, NotFound, slash_command, Embed
from discord.ext import commands

from utils import reporter
from utils.cfg import cfg
from utils.reporter import report_error
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("Metrics")
log.setLevel(cfg["log_level"])


class Metrics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo = motor.motor_asyncio.AsyncIOMotorClient('mongodb://localhost:27017')
        self.db = self.mongo.rocketwatch
        self.collection = self.db.command_metrics
        self.color = Color.from_rgb(235, 142, 85)

    @slash_command(guild_ids=guilds)
    async def command_metrics(self, ctx):
        """
        Show various metrics about the bot.
        """
        await ctx.defer(ephemeral=True)
        try:
            e = Embed(title="Command Metrics (last 7 days)", color=self.color)
            desc = "```\n"
            # last 7 days
            start = datetime.utcnow() - timedelta(days=7)

            # get the total number of handled commands in the last 7 days
            total_commands_handled = await self.collection.count_documents({'timestamp': {'$gte': start}})
            desc += f"Total Commands Handled: {total_commands_handled}\n\n"

            # get the 10 most used commands of the last 7 days
            most_used_commands = await self.collection.aggregate([
                {'$match': {'timestamp': {'$gte': start}}},
                {'$group': {'_id': '$command', 'count': {'$sum': 1}}},
                {'$sort': {'count': -1}}
            ]).to_list(length=10)
            desc += "10 Most Used Commands:\n"
            for command in most_used_commands:
                desc += f" - {command['_id']}: {command['count']}\n"

            e.description = desc + "```"
            await ctx.respond(embed=e, ephemeral=True)
        except Exception as e:
            log.error(f"Failed to get command metrics: {e}")
            await report_error(e)

    @commands.Cog.listener()
    async def on_application_command(self, ctx):
        log.info(f"/{ctx.command.name} triggered by {ctx.author} in #{ctx.channel.name} ({ctx.guild})")
        try:
            await self.collection.insert_one({
                '_id'      : ctx.interaction.id,
                'command'  : ctx.command.name,
                'options'  : ctx.interaction.data.get("options", []),
                'user'     : {
                    'id'  : ctx.author.id,
                    'name': ctx.author.name,
                },
                'guild'    : {
                    'id'  : ctx.guild.id,
                    'name': ctx.guild.name,
                },
                'channel'  : {
                    'id'  : ctx.channel.id,
                    'name': ctx.channel.name,
                },
                "timestamp": datetime.utcnow(),
                'status'   : 'pending'
            })
        except Exception as e:
            log.error(f"Failed to insert command into database: {e}")
            await report_error(e)

    @commands.Cog.listener()
    async def on_application_command_completion(self, ctx):
        log.info(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) completed successfully")

        try:
            await self.collection.update_one({'_id': ctx.interaction.id}, {'$set': {'status': 'completed'}})
        except Exception as e:
            log.error(f"Failed to update command status to completed: {e}")
            await report_error(e)

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx, excep):
        log.info(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) failed")

        try:
            await self.collection.update_one({'_id': ctx.interaction.id}, {'$set': {'status': 'error', 'error': str(excep)}})
        except Exception as e:
            log.error(f"Failed to update command status to error: {e}")
            await report_error(e)

        await reporter.report_error(excep, ctx=ctx)
        msg = f'{ctx.author.mention} An unexpected error occurred. This Error has been automatically reported.'
        try:
            # try to inform the user. this might fail if it took too long to respond
            return await ctx.respond(msg, ephemeral=is_hidden(ctx))
        except NotFound:
            # so fall back to a normal channel message if that happens
            return await ctx.channel.send(msg)

    @commands.Cog.listener()
    async def on_ready(self, ):
        log.info(f'Logged in as {self.bot.user.name} ({self.bot.user.id})')


def setup(bot):
    bot.add_cog(Metrics(bot))
