from discord import Embed, Color
from discord.ext import commands
from discord_slash import cog_ext
from tinydb import TinyDB, Query

from utils.slash_permissions import guilds, owner_only_slash


class FaQ(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = Color.from_rgb(235, 142, 85)
        self.db = TinyDB('./plugins/faq/state.db',
                         create_dirs=True,
                         sort_keys=True,
                         indent=4,
                         separators=(',', ': '))

    async def created_embed(self, data):
        embed = Embed(color=self.color)
        embed.title = f"FAQ: {data['title']}"
        embed.description = data["description"].encode('raw_unicode_escape').decode('unicode_escape')
        embed.set_footer(text=f"Credits: {data['credits']}")
        if data["image"]:
            embed.set_image(url=data["image"])
        return embed

    @owner_only_slash()
    async def store_faq(self, ctx, name, title="", description="", credits="", image_url=""):
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            # only update what has been set
            if title:
                current_state[0]["title"] = title
            if description:
                current_state[0]["description"] = description
            if credits:
                current_state[0]["credits"] = credits
            if image_url:
                current_state[0]["image"] = image_url
        else:
            # create new entry
            self.db.insert({"name": name, "title": title, "description": description, "credits": credits, "image": image_url})
        await ctx.send("Entry Updated!")

    @owner_only_slash()
    async def delete_faq(self, ctx, name):
        # remove entry from db
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            self.db.remove(entries.name == name)
            await ctx.send("Entry Deleted!")
        else:
            await ctx.send(f"No entry named {name}")

    @cog_ext.cog_slash(guild_ids=guilds)
    async def faq(self, ctx, name):
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            await ctx.send(embed=await self.created_embed(current_state[0]))
        else:
            # if no entry found, return list of possible entries
            possible_entries = []
            for entry in self.db.all():
                possible_entries.append(entry["name"])
            await ctx.send(f"No entry named {name}. Possible entries: `{', '.join(possible_entries)}`")

    @cog_ext.cog_slash(guild_ids=guilds)
    async def faq_list(self, ctx):
        entries = Query()
        current_state = self.db.search(entries.name != "")
        if current_state:
            embed = Embed(color=self.color)
            embed.title = "FAQ List"
            for entry in current_state:
                embed.add_field(name=entry["name"], value=f"Name: `{entry['title']}`", inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send("No FAQs found!")


def setup(bot):
    bot.add_cog(FaQ(bot))
