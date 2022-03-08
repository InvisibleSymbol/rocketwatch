from discord.commands import slash_command
from discord.ext import commands
from tinydb import TinyDB, Query

from utils.embeds import Embed
from utils.slash_permissions import guilds, owner_only_slash


class FaQ(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = TinyDB('./plugins/faq/state.db',
                         create_dirs=True,
                         sort_keys=True,
                         indent=4,
                         separators=(',', ': '))

    async def created_embed(self, data):
        e = Embed()
        e.title = f"FAQ: {data['title']}"
        e.description = data["description"].encode('raw_unicode_escape').decode('unicode_escape')
        e.set_footer(text=f"Credits: {data['credits']}")
        if data["image"]:
            e.set_image(url=data["image"])
        return e

    @owner_only_slash()
    async def store_faq(self, ctx, name, title="", description="", credits="", image_url=""):
        entries = Query()
        if current_state := self.db.search(entries.name == name):
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
        await ctx.respond("Entry Updated!")

    @owner_only_slash()
    async def delete_faq(self, ctx, name):
        # remove entry from db
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            self.db.remove(entries.name == name)
            await ctx.respond("Entry Deleted!")
        else:
            await ctx.respond(f"No entry named {name}")

    @slash_command(guild_ids=guilds)
    async def faq(self, ctx, name):
        entries = Query()
        if current_state := self.db.search(entries.name == name):
            await ctx.respond(embed=await self.created_embed(current_state[0]))
        else:
            # if no entry found, return list of possible entries
            possible_entries = [entry["name"] for entry in self.db.all()]
            await ctx.respond(f"No entry named {name}. Possible entries: `{', '.join(possible_entries)}`")

    @slash_command(guild_ids=guilds)
    async def faq_list(self, ctx):
        entries = Query()
        if current_state := self.db.search(entries.name != ""):
            e = Embed()
            e.title = "FAQ List"
            for entry in current_state:
                e.add_field(name=entry["name"], value=f"Name: `{entry['title']}`", inline=False)
            await ctx.respond(embed=e)
        else:
            await ctx.respond("No FAQs found!")


def setup(bot):
    bot.add_cog(FaQ(bot))
