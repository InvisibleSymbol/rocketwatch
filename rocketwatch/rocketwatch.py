import io
import logging
import traceback
from pathlib import Path
from typing import Optional

from discord import (
    app_commands, 
    Interaction,
    Intents,
    Thread, 
    File, 
    Object, 
    User,
)
from discord.abc import GuildChannel, PrivateChannel
from discord.ext import commands
from discord.ext.commands import Bot, Context
from discord.app_commands import CommandTree, AppCommandError

from utils.cfg import cfg
from utils.retry import retry_async

log = logging.getLogger("rocketwatch")
log.setLevel(cfg["log_level"])


class RocketWatch(Bot):
    class RWCommandTree(CommandTree):
        async def on_error(self, interaction: Interaction, error: AppCommandError) -> None:
            bot: RocketWatch = self.client
            ctx = await Context.from_interaction(interaction)
            await bot.on_command_error(ctx, error)
    
    def __init__(self, intents: Intents) -> None:
        super().__init__(command_prefix=(), tree_cls=self.RWCommandTree, intents=intents)
    
    async def _load_plugins(self):
        chain = cfg["rocketpool.chain"]
        storage = cfg["rocketpool.manual_addresses.rocketStorage"]
        log.info(f"Running using storage contract {storage} (Chain: {chain})")

        log.info("Loading plugins...")
        included_modules = set(cfg["modules.include"] or [])
        excluded_modules = set(cfg["modules.exclude"] or [])

        def should_load_plugin(_plugin: str) -> bool:
            # inclusion takes precedence in case of collision
            if _plugin in included_modules:
                log.debug(f"Plugin {_plugin} explicitly included")
                return True
            elif _plugin in excluded_modules:
                log.debug(f"Plugin {_plugin} explicitly excluded")
                return False
            elif len(included_modules) > 0:
                log.debug(f"Plugin {_plugin} implicitly excluded")
                return False
            else:
                log.debug(f"Plugin {_plugin} implicitly included")
                return True

        for path in Path("plugins").glob('**/*.py'):
            plugin_name = path.stem
            if not should_load_plugin(plugin_name):
                log.warning(f"Skipping plugin {plugin_name}")
                continue

            log.info(f"Loading plugin \"{plugin_name}\"")
            try:
                extension_name = f"plugins.{plugin_name}.{plugin_name}"
                await self.load_extension(extension_name)
            except Exception:
                log.exception(f"Failed to load plugin \"{plugin_name}\"")

        log.info('Finished loading plugins')

    async def setup_hook(self) -> None:
        await self._load_plugins()
        
    async def sync_commands(self) -> None:
        log.info("Syncing command tree...")
        await self.tree.sync()
        for guild in self.guilds:
            await self.tree.sync(guild=guild)
        
    def clear_commands(self) -> None:
        self.tree.clear_commands(guild=None)
        for guild in self.guilds:
            self.tree.clear_commands(guild=guild)

    async def on_ready(self):
        log.info(f"Logged in as {self.user.name} ({self.user.id})")
        commands_enabled = cfg["modules.enable_commands"]
        if not commands_enabled:
            log.info("Commands disabled, clearing tree...")
            self.clear_commands()
            if commands_enabled is None:
                log.info("Command sync behavior unspecified, skipping")
                return

        await self.sync_commands()

    async def on_command_error(self, ctx: Context, error: Exception) -> None:
        log.error(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) failed")
        if isinstance(error, commands.errors.MaxConcurrencyReached):
            msg = "Someone else is already using this command. Please try again later."
        elif isinstance(error, app_commands.errors.CommandOnCooldown):
            msg = f"Slow down! You are using this command too fast. Please try again in {error.retry_after:.0f} seconds."
        else:
            msg = "An unexpected error occurred and has been reported to the developer. Please try again later."

        try:
            await self.report_error(error, ctx)
            await ctx.send(content=msg, ephemeral=True)
        except Exception:
            log.exception("Failed to alert user")

    async def get_or_fetch_guild(self, guild_id: int) -> Object:
        return self.get_guild(guild_id) or await self.fetch_guild(guild_id)

    async def get_or_fetch_channel(self, channel_id: int) -> GuildChannel | PrivateChannel | Thread:
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def get_or_fetch_user(self, user_id: int) -> User:
        return self.get_user(user_id) or await self.fetch_user(user_id)

    async def report_error(self, exception: Exception, ctx: Optional[Context] = None, *args) -> None:
        err_description = f"`{repr(exception)[:150]}`"
        
        if args:
            args_fmt = "\n".join(f"args[{i}] = {arg}" for i, arg in enumerate(args))
            err_description += f"\n```{args_fmt}```"
        
        if ctx:
            err_description += (
                f"\n```"
                f"{ctx.command.name = }\n"
                f"ctx.command.params = {getattr(ctx.command, 'params')}\n"
                f"{ctx.channel = }\n"
                f"{ctx.author = }"
                f"```"
            )

        error = getattr(exception, "original", exception)
        err_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        log.error(err_trace)

        try:
            channel = await self.get_or_fetch_channel(cfg["discord.channels.errors"])
            file = File(io.StringIO(err_trace), "exception.txt")
            await retry_async(tries=5, delay=5)(channel.send)(err_description, file=file)
        except Exception:
            log.exception("Failed to send message. Max retries reached.")
