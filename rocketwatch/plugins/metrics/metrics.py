import logging

import motor.motor_asyncio
from discord import Color, NotFound
from discord.ext import commands

from utils import reporter
from utils.cfg import cfg
from utils.reporter import report_error
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

    @commands.Cog.listener()
    async def on_application_command(self, ctx):
        log.info(f"/{ctx.command.name} triggered by {ctx.author} in #{ctx.channel.name} ({ctx.guild})")
        try:
            await self.collection.insert_one({
                '_id': ctx.interaction.id,
                'command': ctx.command.name,
                'options': ctx.interaction.data.get("options", []),
                'user': {
                    'id': ctx.author.id,
                    'name': ctx.author.name,
                },
                'guild': {
                    'id': ctx.guild.id,
                    'name': ctx.guild.name,
                },
                'channel': {
                    'id': ctx.channel.id,
                    'name': ctx.channel.name,
                },
                'status': 'pending'
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
