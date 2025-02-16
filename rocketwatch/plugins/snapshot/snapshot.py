import logging
import requests

from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, TypedDict, Literal

import pymongo
import termplotlib as tpl
from eth_typing import ChecksumAddress
from PIL import Image
from discord import File
from discord.ext import commands
from web3.constants import ADDRESS_ZERO
from graphql_query import Operation, Query, Argument
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


class QueuedSnapshot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = pymongo.MongoClient(cfg["mongodb_uri"]).rocketwatch
        self.version = 3
        self._rate_limit = timedelta(minutes=5)
        self._last_ran = datetime.now() - self._rate_limit

    @staticmethod
    def _query_api(queries: list[Query]) -> dict:
        query_json = {"query": Operation(type="query", queries=queries).render()}
        log.debug(f"Snapshot query: {query_json}")
        response = requests.post("https://hub.snapshot.org/graphql", json=query_json).json()
        if "errors" in response:
            raise Exception(response["errors"])
        return response["data"]

    ProposalState = Literal["active", "closed"]

    class Proposal(TypedDict):
        id: str
        title: str
        choices: list[str]
        state: 'QueuedSnapshot.ProposalState'
        start: int
        end: int
        scores: list[float]
        scores_total: float
        quorum: int

    @staticmethod
    def get_proposals(state: ProposalState, limit=20) -> list[Proposal]:
        query = Query(
            name="proposals",
            arguments=[
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(
                    name="where",
                    value=[
                        Argument(name="space_in", value=["\"rocketpool-dao.eth\""]),
                        Argument(name="state", value=f"\"{state}\"")
                    ]
                ),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc"),
            ],
            fields=[
                "id", "title", "choices", "state", "start", "end",
                "scores", "scores_total", "quorum"
            ]
        )
        return QueuedSnapshot._query_api([query])["proposals"]

    SingleChoice = int
    MultiChoice = list[SingleChoice]
    # weighted votes use strings as keys for some reason
    WeightedChoice = dict[str, int]
    VoteChoice = SingleChoice | MultiChoice | WeightedChoice

    class Vote(TypedDict):
        id: str
        voter: ChecksumAddress
        created: int
        vp: float
        choice: 'QueuedSnapshot.VoteChoice'
        reason: str

    @staticmethod
    def get_votes(proposal: Proposal, limit=1000) -> list[Vote]:
        query = Query(
            name="votes",
            arguments=[
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(
                    name="where",
                    value=[Argument(name="proposal", value=f"\"{proposal['id']}\"")]
                ),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc"),
            ],
            fields=["id", "voter", "created", "vp", "choice", "reason"]
        )
        return QueuedSnapshot._query_api([query])["votes"]

    def _should_run_loop(self) -> bool:
        if (datetime.now() - self._last_ran) < self._rate_limit:
            return False

        self._last_ran = datetime.now()
        return True

    def run_loop(self) -> list[Response]:
        if not self._should_run_loop():
            return []

        if not self.db.snapshot_votes.find_one({"_id": "version", "version": self.version}):
            log.warning("Snapshot version changed, nuking db")
            self.db.snapshot_votes.drop()
            self.db.snapshot_votes.insert_one({"_id": "version", "version": self.version})

        now = datetime.now()
        events = []
        db_updates = []

        proposals = self.get_proposals("active")
        for proposal in proposals:
            log.debug(f"Processing proposal {proposal}")

            current_votes = self.get_votes(proposal)
            proposal_id = proposal["id"]

            previous_votes = {}
            for stored_vote in self.db.snapshot_votes.find({"proposal_id": proposal_id}):
                previous_votes[stored_vote["voter"]] = stored_vote

            for vote in current_votes:
                log.debug(f"Processing vote {vote}")

                prev_vote = previous_votes.get(vote["voter"])
                if prev_vote and prev_vote["choice"] == vote["choice"]:
                    log.debug(f"Same vote choice as before, skipping event")
                    continue
                else:
                    previous_votes[vote["voter"]] = vote

                embed = self.create_vote_embed(proposal, vote, prev_vote)
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

                event_name = "pdao_snapshot_vote_changed" if (vote['vp'] >= 250) else "snapshot_vote_changed"
                event = Response(
                    embed=embed,
                    topic="snapshot",
                    block_number=w3.eth.getBlock("latest").number,
                    event_name=event_name,
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

    def create_vote_embed(self, proposal: Proposal, vote: Vote, prev_vote: Optional[Vote]) -> Optional[Embed]:
        node = rp.call("rocketSignerRegistry.signerToNode", vote["voter"])
        if node == ADDRESS_ZERO:
            # pre Houston vote
            voter = el_explorer_url(vote['voter'])
        else:
            voter = f"{el_explorer_url(node)} ({el_explorer_url(vote['voter'])})"

        vote_fmt = self._format_vote(proposal, vote)
        if vote_fmt is None:
            return None

        embed = Embed(title=proposal['title'])
        if prev_vote is None:
            if len(vote_fmt) <= 20:
                embed.description = f"{voter} voted {vote_fmt}"
            else:
                embed.description = f"{voter} voted\n{vote_fmt}"
        else:
            prev_vote_fmt = self._format_vote(proposal, prev_vote)
            if len(vote_fmt) <= 10 and len(prev_vote_fmt) <= 10:
                embed.description = f"{voter} changed their vote from {prev_vote_fmt} to {vote_fmt}"
            else:
                embed.description = (
                    f"{voter} changed their vote from\n"
                    f"{prev_vote_fmt}\n"
                    f"to\n"
                    f"{vote_fmt}"
                )

        if vote["reason"]:
            max_length = 2000
            reason = vote["reason"]
            if len(embed.description) + len(reason) > max_length:
                suffix = "..."
                overage = len(embed.description) + len(reason) - max_length
                reason = reason[:-(overage + len(suffix))] + suffix

            embed.description += f" ```{reason}```"

        embed.add_field(name="Voting Power", value=f"{vote['vp']:.2f}", inline=False)
        return embed

    def _format_vote(self, proposal: Proposal, vote: Vote) -> Optional[str]:
        match (raw_choice := vote["choice"]):
            case int():
                return self._format_single_choice(proposal, raw_choice)
            case list():
                return self._format_multiple_choice(proposal, raw_choice)
            case dict():
                return self._format_weighted_choice(proposal, raw_choice)
            case _:
                log.error(f"Unknown vote type: {raw_choice}")
                return None

    @staticmethod
    def _label_choice(proposal: Proposal, raw_vote: SingleChoice) -> str:
        # vote choice represented as 1-based index
        return proposal["choices"][raw_vote - 1]

    @staticmethod
    def _format_single_choice(proposal: Proposal, choice: SingleChoice):
        label = QueuedSnapshot._label_choice(proposal, choice)
        match label.lower():
            case "for":
                label = "✅ For"
            case "against":
                label = "❌ Against"
            case "abstain":
                label = "⚪ Abstain"
        return f"`{label}`"

    @staticmethod
    def _format_multiple_choice(proposal: Proposal, choice: MultiChoice) -> str:
        labels = [QueuedSnapshot._label_choice(proposal, c) for c in choice]
        if len(labels) == 1:
            return f"`{labels[0]}`"
        return "**" + "\n".join([f"- {c}" for c in labels]) + "**"

    @staticmethod
    def _format_weighted_choice(proposal: Proposal, choice: WeightedChoice) -> str:
        labels = {QueuedSnapshot._label_choice(proposal, int(c)): w for c, w in choice.items()}
        total_weight = sum(labels.values())
        choice_perc = [(c, round(100 * w / total_weight)) for c, w in labels.items()]
        choice_perc.sort(key=lambda x: x[1], reverse=True)
        graph = tpl.figure()
        graph.barh(
            [x[1] for x in choice_perc],
            [x[0] for x in choice_perc],
            force_ascii = True,
            max_width = 15
        )
        return "```" + graph.get_string().replace("]", "%]") + "```"


    # TODO rewrite hybrid command
    @hybrid_command()
    async def snapshot_votes(self, ctx: Context):
        """
        Show currently active Snapshot votes.
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        e = Embed()
        e.set_author(name="🔗 Data from snapshot.org", url="https://vote.rocketpool.net/#/")
        proposals = self.get_proposals("active")
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
                color = {
                    # 1st rank, gold
                    0: (255, 215, 0),
                    # 2nd rank, silver
                    1: (192, 192, 192),
                    # 3rd rank, bronze
                    2: (205, 127, 50),
                }.get(i, (128, 128, 128))
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
