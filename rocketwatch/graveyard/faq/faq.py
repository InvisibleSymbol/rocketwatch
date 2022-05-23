from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command
from tinydb import TinyDB, Query
from utils.slash_permissions import guilds

from utils.embeds import Embed


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

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def store_faq(self, ctx: Context, name, title="", description="", credits="", image_url=""):
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
            self.db.insert(
                {"name": name, "title": title, "description": description, "credits": credits, "image": image_url})
        await ctx.send(content="Entry Updated!")

    @hybrid_command()
    @guilds(Object(id=cfg["discord.owner.server_id"]))
    @is_owner()
    async def delete_faq(self, ctx: Context, name):
        # remove entry from db
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            self.db.remove(entries.name == name)
            await ctx.send(content="Entry Deleted!")
        else:
            await ctx.send(content=f"No entry named {name}")

    @hybrid_command()
    async def faq(self, ctx: Context, name):
        entries = Query()
        current_state = self.db.search(entries.name == name)
        if current_state:
            await ctx.send(embed=await self.created_embed(current_state[0]))
        else:
            # if no entry found, return list of possible entries
            possible_entries = []
            for entry in self.db.all():
                possible_entries.append(entry["name"])
            await ctx.send(content=f"No entry named {name}. Possible entries: `{', '.join(possible_entries)}`")

    @hybrid_command()
    async def faq_list(self, ctx: Context):
        entries = Query()
        current_state = self.db.search(entries.name != "")
        if current_state:
            e = Embed()
            e.title = "FAQ List"
            for entry in current_state:
                e.add_field(name=entry["name"], value=f"Name: `{entry['title']}`", inline=False)
            await ctx.send(embed=e)
        else:
            await ctx.send(content="No FAQs found!")


async def setup(bot):
    await bot.add_cog(FaQ(bot))
