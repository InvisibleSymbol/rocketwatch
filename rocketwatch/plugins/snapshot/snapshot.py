import logging
import requests

from io import BytesIO
from typing import Optional
from datetime import datetime, timedelta

import pymongo
import termplotlib as tpl
from PIL import Image
from discord import File
from discord.ext import commands
from web3.constants import ADDRESS_ZERO
from discord.ext.commands import Context, hybrid_command

from utils.cfg import cfg
from utils.containers import Response
from utils.draw import BetterImageDraw
from utils.embeds import Embed, el_explorer_url
from utils.readable import uptime
from utils.shared_w3 import w3
from utils.visibility import is_hidden_weak
from utils.rocketpool import rp

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
        self.db = pymongo.MongoClient(cfg["mongodb_uri"]).rocketwatch
        self.version = 3
        self.__rate_limit = timedelta(minutes=5)
        self.__last_ran = datetime.now() - self.__rate_limit

    @staticmethod
    def get_active_proposals():
        query = """
        {
          proposals(
            first: 20,
            skip: 0,
            where: {
                space_in: ["rocketpool-dao.eth"],
                state: "active"
            },
            orderBy: "created",
            orderDirection: desc
          ) {
            id
            title
            choices
            state
            scores
            scores_total
            scores_updated
            end
            quorum
          }
        }
        """
        response = requests.post("https://hub.snapshot.org/graphql", json={"query": query}).json()
        if "errors" in response:
            raise Exception(response["errors"])

        return response["data"]["votes"], response["data"]["proposal"]

    @staticmethod
    def get_votes(snapshot_id: int):
        query = f"""
        {{
          votes (
            first: 1000
            skip: 0
            where: {{
              proposal: "{snapshot_id}"
            }}
            orderBy: "created",
            orderDirection: desc
          ) {{
            id
            voter
            created
            vp
            choice
            reason
          }}
          proposal(
            id:"{snapshot_id}"
          ) {{
            choices
            title
          }}
        }}
        """
        response = requests.post("https://hub.snapshot.org/graphql", json={"query": query}).json()
        if "errors" in response:
            raise Exception(response["errors"])

        return response["data"]["votes"], response["data"]["proposal"]

    def __should_run_loop(self) -> bool:
        if (datetime.now() - self.__last_ran) < self.__rate_limit:
            return False

        self.__last_ran = datetime.now()
        return True

    def run_loop(self) -> list[Response]:
        if not self.__should_run_loop():
            return []

        if not self.db.snapshot_votes.find_one({"_id": "version", "version": self.version}):
            log.warning("Snapshot version changed, nuking db")
            self.db.snapshot_votes.drop()
            self.db.snapshot_votes.insert_one({"_id": "version", "version": self.version})

        now = datetime.now()
        events = []
        db_updates = []

        proposals = self.get_active_proposals()

        for proposal in proposals:
            log.debug(f"Processing proposal {proposal}")

            proposal_id = proposal["id"]
            current_votes, _ = self.get_votes(proposal_id)
            if proposal_id not in ["0x129eaa1779916b96fa1a34c7f9e24f87abad820c8fbe8ea2663f170891295e2e"]:
                continue

            previous_votes = {}
            for stored_vote in self.db.snapshot_votes.find({"proposal_id":proposal_id}):
                previous_votes[stored_vote["voter"]] = stored_vote

            # compare the two
            for vote in current_votes[:25]:
                log.debug(f"Processing vote {vote}")

                prev_vote = previous_votes.get(vote["voter"])
                if prev_vote and prev_vote["choice"] == vote["choice"]:
                    log.debug(f"Same vote choice as before, skipping event")
                    continue
                else:
                    previous_votes[vote["voter"]] = vote

                embed = self.handle_vote(proposal, vote, prev_vote)
                if embed is None:
                    continue

                embed.set_author(
                    name="🔗 Data from snapshot.org",
                    url=f"https://vote.rocketpool.net/#/proposal/{proposal_id}"
                )

                db_update = {
                    "proposal_id": proposal_id,
                    "voter"      : vote["voter"],
                    "choice"     : vote["choice"],
                    "timestamp"  : now
                }
                db_updates.append(db_update)

                event = Response(
                    embed=embed,
                    topic="snapshot",
                    block_number=w3.eth.getBlock("latest").number,
                    event_name="pdao_snapshot_vote_changed" if (vote['vp'] >= 250) else "snapshot_vote_changed",
                    unique_id="_".join((str(v) for v in db_update.values()))
                )
                events.append(event)

        if db_updates:
            self.db.snapshot_votes.bulk_write([
                pymongo.UpdateOne(
                    {"proposal_id": update["proposal_id"], "voter": update["voter"]},
                    {"$set": update},
                    upsert=True
                ) for update in db_updates
            ])

        return events

    def handle_vote(self, proposal: dict, vote: dict, prev_vote: Optional[dict]) -> Optional[Embed]:
        def label_vote(_raw_vote: int) -> str:
            # vote choice represented as 1-based index
            return proposal["choices"][_raw_vote - 1]

        match (raw_choice := vote["choice"]):
            case int():
                label_fn = label_vote
                handle_fn = self.handle_single_choice_vote
            case list():
                label_fn = lambda v: [label_vote(c) for c in v]
                handle_fn = self.handle_multiple_choice_vote
            case dict():
                # weighted votes use strings as keys for some reason
                label_fn = lambda v: {label_vote(int(c)): w for c,w in v.items()}
                handle_fn = self.handle_weighted_vote
            case _:
                log.error(f"Unknown vote type: {raw_choice}")
                return None

        node = rp.call("rocketSignerRegistry.signerToNode", vote["voter"])
        if node == ADDRESS_ZERO:
            # pre Houston vote
            voter = el_explorer_url(vote['voter'])
        else:
            voter = f"{el_explorer_url(node)} ({el_explorer_url(vote['voter'])})"

        embed = Embed(title=proposal['title'])
        if prev_vote is None:
            new_choice = label_fn(vote["choice"])
            embed.description = handle_fn(voter, new_choice, None)
        else:
            new_choice = label_fn(vote["choice"])
            old_choice = label_fn(prev_vote["choice"])
            embed.description = handle_fn(voter, new_choice, old_choice)

        if vote["reason"]:
            max_length = 2000
            reason = vote["reason"]
            reason_fmt = f"```{reason}```"
            if len(embed.description) + len(reason_fmt) > max_length:
                suffix = "..."
                overage = len(embed.description) + len(reason_fmt) - max_length
                reason_fmt = f"```{reason[:-(overage + len(suffix))]}{suffix}```"

            embed.description += reason_fmt

        embed.add_field(name="Voting Power", value=f"{vote['vp']:.2f}", inline=False)

        return embed

    @staticmethod
    def handle_single_choice_vote(voter: str, choice: str, prev_choice: Optional[str]) -> str:
        def fmt_vote(_choice: str) -> str:
            match _choice.lower():
                case "for":
                    return "`✅ For`"
                case "against":
                    return "`❌ Against`"
                case "abstain":
                    return "`⚪ Abstain`"
            return f"`{choice}`"

        if prev_choice:

            return f"{voter} changed their vote from {fmt_vote(prev_choice)} to {fmt_vote(choice)}"
        else:
            return f"{voter} voted `{fmt_vote(choice)}`"

    @staticmethod
    def handle_multiple_choice_vote(voter: str, choice: list[str], prev_choice: Optional[list[str]]) -> str:
        def fmt_choice(_choice: list[str]) -> str:
            return "**" + "\n".join([f"- {c}" for c in _choice]) + "**"

        if prev_choice:
            return (
                f"{voter} changed their vote from\n"
                f"{fmt_choice(prev_choice)}\n"
                f"to\n"
                f"{fmt_choice(choice)}"
            )
        else:
            return (
                f"{voter} voted\n"
                f"{fmt_choice(choice)}"
            )

    @staticmethod
    def handle_weighted_vote(voter: str, choice: dict[str, int], prev_choice: Optional[dict[str, int]]) -> str:
        def fmt_choice(_choice: dict[str, int]) -> str:
            total_weight = sum(choice.values())
            choice_perc = [(c, round(100 * w / total_weight)) for c, w in choice.items()]
            choice_perc.sort(key=lambda x: x[1], reverse=True)
            graph = tpl.figure()
            graph.barh([x[1] for x in choice_perc], [x[0] for x in choice_perc], max_width=20)
            return "```" + graph.get_string().replace("]", "%]") + "```"

        if prev_choice:
            return (
                f"{voter} changed their vote from\n"
                f"{fmt_choice(prev_choice)}\n"
                f"to\n"
                f"{fmt_choice(choice)}"
            )
        else:
            return (
                f"{voter} voted\n"
                f"{fmt_choice(choice)}"
            )

    # TODO rewrite hybrid command
    @hybrid_command()
    async def snapshot_votes(self, ctx: Context):
        """
        Show currently active Snapshot votes.
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        e = Embed()
        e.set_author(name="🔗 Data from snapshot.org", url="https://vote.rocketpool.net/#/")
        proposals = self.get_active_proposals()
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

        def safe_div(x, y):
            return (x / y) if y != 0 else 0

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
            max_scores = max(proposal["scores"])
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
                    safe_div(scores, max_scores),
                    primary=color,
                )
                # show percentage next to progress bar (max 40 pixels)
                draw.dynamic_text(
                    (x_offset + 50, y_offset),
                    f"{safe_div(scores, proposal['scores_total']):.0%}",
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
