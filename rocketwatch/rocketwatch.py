import io
import logging
import traceback
from pathlib import Path
from typing import Optional

from discord import TextChannel, File, app_commands, NotFound
from discord.ext.commands import Bot, Context
from discord.ext import commands

from utils.cfg import cfg
from utils.retry import retry_async

log = logging.getLogger("rocketwatch")
log.setLevel(cfg["log_level"])


class RocketWatch(Bot):
    async def on_ready(self):
        log.info(f'Logged in as {self.user.name} ({self.user.id})')

    async def setup_hook(self) -> None:
        chain = cfg["rocketpool.chain"]
        storage = cfg['rocketpool.manual_addresses.rocketStorage']
        log.info(f"Running using storage contract {storage} (Chain: {chain})")

        log.info('Loading plugins')
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

    async def on_command_error(self, ctx: Context, exception: Exception) -> None:
        log.info(f"/{ctx.command.name} called by {ctx.author} in #{ctx.channel.name} ({ctx.guild}) failed")
        if isinstance(exception, commands.errors.MaxConcurrencyReached):
            msg = f"Someone else is already using this command. Please try again later"
        elif isinstance(exception, app_commands.errors.CommandOnCooldown):
            msg = f"Slow down! You are using this command too fast. Please try again in {exception.retry_after:.0f} seconds"
        else:
            msg = f"An unexpected error occurred and has been reported to the developer. Please try again later"

        try:
            await self.report_error(exception, ctx)
            await ctx.send(content=msg, ephemeral=True)
        except Exception:
            log.exception("Failed to alert user")

    async def get_or_fetch_channel(self, channel_id: int) -> TextChannel:
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def report_error(self, exception: Exception, ctx: Optional[Context] = None, *args) -> None:
        err_description = f"`{repr(exception)[:100]}`"
        if args:
            args_fmt = "\n".join(f"args[{i}] = {arg}" for i, arg in enumerate(args))
            err_description += f"\n```{args_fmt}```"
        if ctx:
            err_description += (
                f"\n```"
                f"{ctx.command.name = }\n"
                f"{ctx.command.params = }\n"
                f"{ctx.channel = }\n"
                f"{ctx.author = }"
                f"```"
            )

        error = getattr(exception, "original", exception)
        err_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        log.error(err_trace)

        channel = await self.get_or_fetch_channel(cfg["discord.channels.errors"])

        @retry_async(tries=5, delay=5)
        async def _send_error_message():
            fp = io.BytesIO(err_trace.encode())
            await channel.send(err_description, file=File(fp, "exception.txt"))

        try:
            await _send_error_message()
        except Exception:
            log.exception(f"Failed to send message. Max retries reached.")
