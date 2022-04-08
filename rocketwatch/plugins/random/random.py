import logging
from datetime import datetime

import pytz
from discord.commands import slash_command
from discord.ext import commands

from utils.cfg import cfg
from utils.embeds import Embed, ens, el_explorer_url
from utils.sea_creatures import sea_creatures, get_sea_creature_for_address
from utils.shared_w3 import w3
from utils.slash_permissions import guilds
from utils.visibility import is_hidden

log = logging.getLogger("random")
log.setLevel(cfg["log_level"])


class Random(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @slash_command(guild_ids=guilds)
    async def dev_time(self, ctx):
        """Timezones too confusing to you? Well worry no more, this command is here to help!"""
        e = Embed()
        time_format = "%A %H:%M:%S %Z"

        dev_time = datetime.now(tz=pytz.timezone("UTC"))
        # seconds since midnight
        midnight = dev_time.replace(hour=0, minute=0, second=0, microsecond=0)
        percentage_of_day = (dev_time - midnight).seconds / (24 * 60 * 60)
        # convert to uint16
        uint_day = int(percentage_of_day * 65535)
        # generate binary string
        binary_day = f"{uint_day:016b}"
        e.add_field(name="Coorddinated Universal Time",
                    value=f"{dev_time.strftime(time_format)}\n"
                          f"`{binary_day} (0x{uint_day:04x})`",
                    inline=False)

        dev_time = datetime.now(tz=pytz.timezone("Australia/Lindeman"))
        e.add_field(name="Time for most of the Dev Team", value=dev_time.strftime(time_format), inline=False)

        joe_time = datetime.now(tz=pytz.timezone("America/New_York"))
        e.add_field(name="Joe's Time", value=joe_time.strftime(time_format), inline=False)

        await ctx.respond(embed=e)

    @slash_command(guild_ids=guilds)
    async def sea_creatures(self, ctx, address: str = None):
        """List all sea creatures with their required minimum holding"""
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed()
        if address is not None:
            try:
                if ".eth" in address:
                    address = ens.resolve_name(address)
                address = w3.toChecksumAddress(address)
            except (ValueError, TypeError):
                e.description = "Invalid address"
                await ctx.respond(embed=e, ephemeral=is_hidden(ctx))
                return
            creature = get_sea_creature_for_address(address)
            if not creature:
                e.description = f"No sea creature for {address}"
            else:
                # get the required holding from the dictionary
                holding = [h for h, c in sea_creatures.items() if c == creature[0]][0]
                e.add_field(name="Visualization", value=el_explorer_url(address, prefix=creature), inline=False)
                e.add_field(name="Required holding", value=f"{holding * len(creature)} ETH", inline=False)
        else:
            e.title = "Possible Sea Creatures"
            e.description = "RPL (both old and new), rETH and ETH are consider as assets for the sea creature determination!"
            for holding_value, sea_creature in sea_creatures.items():
                e.add_field(name=f"{sea_creature}:", value=f"holds over {holding_value} ETH worth of assets", inline=False)
        await ctx.respond(embed=e, ephemeral=is_hidden(ctx))
        return


def setup(bot):
    bot.add_cog(Random(bot))
