import datetime
import json

from discord.ext import commands
from web3.datastructures import MutableAttributeDict as aDict

from utils.embeds import assemble
from utils.slash_permissions import owner_only_slash


class Debug(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

    with open("./plugins/mock/mock_data.json") as f:
      data = json.load(f)

    self.mock_mapping = data["mapping"]
    self.mock_data = data["data"]

  @owner_only_slash()
  async def mock(self, ctx, event_name):
    if event_name not in self.mock_mapping:
      return await ctx.send(f"No Mock Mapping available for this Event")

    args = aDict({})
    args.event_name = event_name
    args.timestamp = int(datetime.datetime.now().timestamp())
    for arg in self.mock_mapping[event_name]:
      args[arg] = self.mock_data[arg]

    embed = assemble(args)
    # add note to footer about it being a mock
    embed.set_footer(text=embed._footer["text"] + " · This is a mocked Event!")

    # trick to remove the command call message
    tmp = await ctx.send("done")
    await tmp.delete()

    await ctx.channel.send(embed=embed)


def setup(bot):
  bot.add_cog(Debug(bot))
