import logging

from discord import Color, NotFound
from discord.ext import commands

from utils import reporter
from utils.cfg import cfg
from utils.visibility import is_hidden

log = logging.getLogger("Metrics")
log.setLevel(cfg["log_level"])


class Metrics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)

    @commands.Cog.listener()
    async def on_application_command_completion(self, ctx):
        log.info(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) completed successfully")

    @commands.Cog.listener()
    async def on_application_command(self, ctx):
        log.info(f"/{ctx.command.name} triggered by {ctx.author} in #{ctx.channel.name} ({ctx.guild})")

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx, excep):
        log.info(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) failed")
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
