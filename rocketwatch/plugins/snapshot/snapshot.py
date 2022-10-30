import logging
from datetime import datetime
from io import BytesIO

import pymongo
from PIL import Image
from discord import File
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands import hybrid_command

from utils.cfg import cfg
from utils.containers import Response
from utils.draw import BetterImageDraw
from utils.embeds import Embed, el_explorer_url
from utils.readable import uptime
from utils.shared_w3 import w3
from utils.thegraph import get_active_snapshot_proposals, get_votes_of_snapshot
from utils.visibility import is_hidden_weak

log = logging.getLogger("snapshot")
log.setLevel(cfg["log_level"])

RANK_COLORS = {
    # 1st rank, gold
    0: (255, 215, 0),
    # 2nd rank, silver
    1: (192, 192, 192),
    # 3rd rank, bronze
    2: (205, 127, 50),
}


class QueuedSnapshot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo = pymongo.MongoClient(cfg["mongodb_uri"])
        self.db = self.mongo.rocketwatch
        self.ratelimit = 60
        self.last_ran = datetime.now()

    def run_loop(self):
        # ratelimit
        if (datetime.now() - self.last_ran).seconds < self.ratelimit:
            return []
        self.last_ran = datetime.now()
        current_proposals = get_active_snapshot_proposals()

        now = datetime.now()
        updates = []
        events = []
        for proposal in current_proposals:
            # get the current votes for this proposal
            current_votes, _ = get_votes_of_snapshot(proposal["id"])
            # get the previous votes for this proposal
            previous_votes = list(self.db.snapshot_votes.find({"proposal_id": proposal["id"]}))
            # compare the two
            for vote in current_votes:
                # skip the vote entirely if the voting power is too low
                # check if the vote is already in the db and if it is old enough
                prev_vote = next((v for v in previous_votes if v["voter"] == vote["voter"]), None)
                if prev_vote and (now - prev_vote["timestamp"]).total_seconds() < 300:
                    continue
                # make sure the vote actually changed
                if prev_vote and prev_vote["choice"] == vote["choice"]:
                    continue
                # update the db
                updates.append({
                    "proposal_id": proposal["id"],
                    "voter"      : vote["voter"],
                    "choice"     : vote["choice"],
                    "timestamp"  : now,
                })
                if not previous_votes:
                    continue
                # create change embed
                # important: choices are indexes, use the proposal.choices array to get the actual choice
                new_choices = [proposal["choices"][c - 1] for c in vote["choice"]]
                e = Embed(
                    title=f"Snapshot Vote {'Changed' if prev_vote else 'Added'}",
                )
                nl = "\n- "
                if prev_vote:
                    e.description = f"**{el_explorer_url(vote['voter'])}** changed their vote from\n"
                    old_choices = [proposal["choices"][c - 1] for c in prev_vote["choice"]] if prev_vote else []
                    e.description += f"**- {nl.join(old_choices)}**\nto\n**- {nl.join(new_choices)}**"
                else:
                    e.description = f"**{el_explorer_url(vote['voter'])}** voted for\n**- {nl.join(new_choices)}**"
                e.description += f"\n\n**Voting Power:** {vote['vp']:.2f}"
                # add the proposal link
                e.set_author(name="🔗 Data from snapshot.org", url=f"https://vote.rocketpool.net/#/proposal/{proposal['id']}")
                events.append(Response(
                    embed=e,
                    topic="snapshot",
                    block_number=w3.eth.getBlock("latest").number,
                    event_name="snapshot_vote_changed",
                    unique_id=f"{proposal['id']}_{vote['voter']}_{'_'.join(new_choices)}_{now.timestamp()}",
                ))
        if updates:
            # update or insert the votes
            self.db.snapshot_votes.bulk_write([
                pymongo.UpdateOne(
                    {"proposal_id": u["proposal_id"], "voter": u["voter"]},
                    {"$set": u},
                    upsert=True,
                ) for u in updates
            ])
        return events

    @hybrid_command()
    async def analyze_pairs_max_leb(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        vote_id = '0x7426469ae1f7c6de482ab4c2929c3e29054991601c95f24f4f4056d424f9f671'
        votes, proposal = get_votes_of_snapshot(vote_id)
        # votes is an array of votes that include a 'choice' field
        # the choice field is an array of choice indices
        e = Embed()
        e.set_author(name="🔗 Data from snapshot.org", url="https://snapshot.org/#/delegate/rocketpool-dao.eth")
        e.title = f"Snapshot Proposal: {proposal['title']}"
        # we want to find out the amount of voting power for each pair of choices

        voting_pairs = {}
        choice_mapping = {
            1: "nETH",
            2: "fixed ETH",
            3: "pETH",
            4: "stupid",
            5: "dumb"
        }

        for vote in votes:
            # resolve the mapping of choice indices to choice names
            choices = [choice_mapping.get(i, "???") for i in vote['choice']]
            # sort the choices so that we can use them as a key
            choices.sort()
            # convert the choices to a string so that we can use them as a key
            choices_str = ' & '.join(choices)
            if choices_str not in voting_pairs:
                voting_pairs[choices_str] = [0, 0]
            voting_pairs[choices_str][0] += vote['vp']
            voting_pairs[choices_str][1] += 1

        # create a ranking of the pairs
        ranking = sorted(voting_pairs.items(), key=lambda x: x[1], reverse=True)

        des = "```"
        for i, (pair, power) in enumerate(ranking):
            des += f"{i + 1}. {pair}\n\t{power[0]:.2f} votes ({power[1]} voters)\n"

        e.description = f"{des}```"
        await ctx.send(embed=e)

    @hybrid_command()
    async def votes(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        e = Embed()
        e.set_author(name="🔗 Data from snapshot.org", url="https://snapshot.org/#/delegate/rocketpool-dao.eth")
        proposals = get_active_snapshot_proposals()
        if not proposals:
            e.description = "No active proposals"
            return await ctx.send(embed=e)

        # image width is based upon the number of proposals
        p_width = 400
        width = p_width * len(proposals)
        # image height is based upon the max number of possible options
        height = 50 * max(len(p["choices"]) for p in proposals) + 170
        # pillow image
        img = Image.new("RGB", (width, height), color=(40, 40, 40))
        # pillow draw
        draw = BetterImageDraw(img)
        # visualize the proposals
        for i, proposal in enumerate(proposals):
            x_offset = i * p_width
            y_offset = 20
            # draw the proposal title
            draw.dynamic_text(
                (x_offset + 10, y_offset),
                proposal["title"],
                20,
                max_width=p_width - 20,
            )
            y_offset += 40
            # order (choice, score) pairs by score
            choices = sorted(zip(proposal["choices"], proposal["scores"]), key=lambda x: x[1], reverse=True)
            for i, (choice, scores) in enumerate(choices):
                draw.dynamic_text(
                    (x_offset + 10, y_offset),
                    choice,
                    15,
                    max_width=p_width - 20 - 120,
                )
                # display the score as text, right aligned
                draw.dynamic_text(
                    (x_offset + p_width - 10, y_offset),
                    f"{scores:,.2f} votes",
                    15,
                    max_width=120,
                    anchor="rt"
                )
                y_offset += 20
                # color first place as golden, second place as silver, third place as bronze, rest as gray
                color = RANK_COLORS.get(i, (128, 128, 128))
                draw.progress_bar(
                    (x_offset + 10 + 50, y_offset),
                    (10, p_width - 30 - 50),
                    scores / proposal["scores_total"],
                    primary=color,
                )
                # show percentage next to progress bar (max 40 pixels)
                draw.dynamic_text(
                    (x_offset + 50, y_offset),
                    f"{scores / proposal['scores_total']:.0%}",
                    15,
                    max_width=45,
                    anchor="rt"
                )
                y_offset += 30
            # title "Quorum"
            draw.dynamic_text(
                (x_offset + 10, y_offset),
                "Quorum:",
                20,
                max_width=p_width - 20,
            )
            y_offset += 30
            # show quorum as a progress bar, (capped at 100%) with the percentage next to it
            draw.progress_bar(
                (x_offset + 10 + 50, y_offset),
                (10, p_width - 30 - 50),
                min(proposal["scores_total"] / proposal["quorum"], 1),
                primary=(64, 255, 64) if proposal["scores_total"] >= proposal["quorum"] else (255, 64, 64),
            )
            draw.dynamic_text(
                (x_offset + 50, y_offset),
                f"{proposal['scores_total'] / proposal['quorum']:.0%}",
                15,
                max_width=45,
                anchor="rt"
            )
            y_offset += 30
            # show how much time is left using the "end" timestamp
            d = proposal["end"] - datetime.now().timestamp()
            draw.dynamic_text(
                (x_offset + 10 + (p_width / 2), y_offset),
                f"{uptime(d)} left",
                15,
                max_width=p_width - 20,
                anchor="mt"
            )

        # save the image to a buffer
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        # send the image
        e.set_image(url="attachment://votes.png")
        await ctx.send(embed=e, file=File(buffer, "votes.png"))


async def setup(bot):
    await bot.add_cog(QueuedSnapshot(bot))
