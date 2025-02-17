import math
import logging
import requests

from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, TypedDict, Literal

import pymongo
import numpy as np
import termplotlib as tpl

from eth_typing import ChecksumAddress
from PIL import Image
from discord import File
from web3.constants import ADDRESS_ZERO
from graphql_query import Operation, Query, Argument
from discord.ext.commands import Context, hybrid_command

from utils.cfg import cfg
from utils.containers import Event
from utils.draw import BetterImageDraw
from utils.embeds import Embed, el_explorer_url
from utils.readable import uptime
from utils.shared_w3 import w3
from utils.visibility import is_hidden_weak
from utils.rocketpool import rp
from utils.submodule import QueuedSubmodule

log = logging.getLogger("snapshot")
log.setLevel(cfg["log_level"])


class Snapshot(QueuedSubmodule):
    def __init__(self, bot):
        super().__init__(bot, timedelta(minutes=5))
        self.db = pymongo.MongoClient(cfg["mongodb_uri"]).rocketwatch
        self.version = 3

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
        body: str
        choices: list[str]
        start: int
        end: int
        scores: list[float]
        scores_total: float
        quorum: int

    @staticmethod
    def get_proposals(state: ProposalState, limit: int = 20, p_id: Optional[str] = None) -> list[Proposal]:
        proposal_filter = [
            Argument(name="space_in", value=["\"rocketpool-dao.eth\""]),
            Argument(name="state", value=f"\"{state}\"")
        ]
        if p_id:
            proposal_filter.append(Argument(name="id", value=f"\"{p_id}\""))

        query = Query(
            name = "proposals",
            arguments = [
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(name="where", value=proposal_filter),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc"),
            ],
            fields = [
                "id", "title", "body", "choices",
                "start", "end", "scores", "scores_total", "quorum"
            ]
        )
        return Snapshot._query_api([query])["proposals"]

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
        choice: 'Snapshot.VoteChoice'
        reason: str

    @staticmethod
    def get_votes(proposal: Proposal, limit=1000) -> list[Vote]:
        query = Query(
            name = "votes",
            arguments = [
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(
                    name = "where",
                    value = [Argument(name="proposal", value=f"\"{proposal['id']}\"")]
                ),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc"),
            ],
            fields = ["id", "voter", "created", "vp", "choice", "reason"]
        )
        return Snapshot._query_api([query])["votes"]

    def _run(self) -> list[Event]:
        if not self.db.snapshot_votes.find_one({"_id": "version", "version": self.version}):
            log.warning("Snapshot version changed, nuking db")
            self.db.snapshot_votes.drop()
            self.db.snapshot_votes.insert_one({"_id": "version", "version": self.version})

        now = datetime.now()
        events: list[Event] = []
        db_updates: list[dict] = []

        known_active_proposals: set[str] = set()
        for stored_proposal in self.db.snapshot_proposals.find():
            if stored_proposal["end"] > now.timestamp():
                known_active_proposals.add(stored_proposal["_id"])
            else:
                # stored proposal ended, emit event and delete from DB
                log.info(f"Found expired proposal: {stored_proposal}")
                # recover full proposal
                proposal = self.get_proposals("closed", p_id=stored_proposal["_id"])[0]
                event = self._create_proposal_end_event(proposal)
                self.db.snapshot_proposals.delete_one(stored_proposal)
                events.append(event)

        active_proposals = self.get_proposals("active")
        for proposal in active_proposals:
            log.debug(f"Processing proposal {proposal}")
            if proposal["id"] not in known_active_proposals:
                # not aware of this proposal yet, emit event and insert into DB
                log.info(f"Found new proposal: {proposal}")
                event = self._create_proposal_start_event(proposal)
                self.db.snapshot_proposals.insert_one({
                    "_id"  : proposal["id"],
                    "start": proposal["start"],
                    "end"  : proposal["end"]
                })
                events.append(event)

            current_votes = self.get_votes(proposal)
            proposal_id = proposal["id"]

            previous_votes: dict[ChecksumAddress, dict] = {}
            for stored_vote in self.db.snapshot_votes.find({"proposal_id": proposal_id}):
                previous_votes[stored_vote["voter"]] = stored_vote

            for vote in current_votes:
                log.debug(f"Processing vote {vote}")

                prev_vote = previous_votes.get(vote["voter"])
                if prev_vote and prev_vote["choice"] == vote["choice"]:
                    log.debug(f"Same vote choice as before, skipping event")
                    continue

                previous_votes[vote["voter"]] = vote

                event = self._create_vote_event(proposal, vote, prev_vote)
                if event is None:
                    continue

                events.append(event)
                db_update = {
                    "proposal_id": proposal_id,
                    "voter"      : vote["voter"],
                    "choice"     : vote["choice"],
                    "timestamp"  : now
                }
                db_updates.append(db_update)

        if db_updates:
            self.db.snapshot_votes.bulk_write([
                pymongo.UpdateOne(
                    {"proposal_id": update["proposal_id"], "voter": update["voter"]},
                    {"$set": update},
                    upsert=True
                ) for update in db_updates
            ])

        return events

    @staticmethod
    def __create_proposal_embed(proposal: Proposal) -> Embed:
        embed = Embed()
        embed.set_author(
            name="🔗 Data from snapshot.org",
            url=f"https://vote.rocketpool.net/#/proposal/{proposal['id']}"
        )
        return embed

    @staticmethod
    def _create_proposal_start_event(proposal: Proposal) -> Event:
        embed = Snapshot.__create_proposal_embed(proposal)
        embed.title = ":bulb: New Snapshot Proposal"

        width = 500
        img = Image.new("RGB", (width, 130 + 40 * len(proposal["choices"])), color=(43, 45, 49))
        Snapshot.draw_proposal(BetterImageDraw(img), proposal, width, 0, 0)

        return Event(
            embed=embed,
            topic="snapshot",
            block_number=w3.eth.getBlock("latest").number,
            event_name="pdao_snapshot_vote_start",
            unique_id=f"{proposal['id']}:event_start",
            attachment=img
        )

    @staticmethod
    def _create_proposal_end_event(proposal: Proposal) -> Event:
        reached_quorum = proposal["scores_total"] >= proposal["quorum"]
        winning_choice = proposal["choices"][np.argmax(proposal["scores"])]

        embed = Snapshot.__create_proposal_embed(proposal)
        if reached_quorum and ("against" not in winning_choice.lower()):
            # potentially fails if abstain > against > for
            embed.title = ":white_check_mark: Snapshot Proposal Passed"
        else:
            embed.title = ":x: Snapshot Proposal Failed"

        width = 500
        img = Image.new("RGB", (width, 130 + 40 * len(proposal["choices"])), color=(43, 45, 49))
        Snapshot.draw_proposal(BetterImageDraw(img), proposal, width, 0, 0)

        return Event(
            embed=embed,
            topic="snapshot",
            block_number=w3.eth.getBlock("latest").number,
            event_name="pdao_snapshot_vote_end",
            unique_id=f"{proposal['id']}:event_end",
            attachment=img
        )

    def _create_vote_event(self, proposal: Proposal, vote: Vote, prev_vote: Optional[Vote]) -> Optional[Event]:
        node = rp.call("rocketSignerRegistry.signerToNode", vote["voter"])
        if node == ADDRESS_ZERO:
            # pre Houston vote
            voter = el_explorer_url(vote['voter'])
        else:
            voter = f"{el_explorer_url(node)} ({el_explorer_url(vote['voter'])})"

        vote_fmt = self._format_vote(proposal, vote)
        if vote_fmt is None:
            return None

        embed = Snapshot.__create_proposal_embed(proposal)
        embed.title = f":ballot_box: {proposal['title']}"

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

        event_name = "pdao_snapshot_vote_changed" if (vote['vp'] >= 250) else "snapshot_vote_changed"
        return Event(
            embed=embed,
            topic="snapshot",
            block_number=w3.eth.getBlock("latest").number,
            event_name=event_name,
            unique_id=f"{proposal['id']}_{vote['voter']}_{vote['created']}:vote"
        )

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
        label = Snapshot._label_choice(proposal, choice)
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
        labels = [Snapshot._label_choice(proposal, c) for c in choice]
        if len(labels) == 1:
            return f"`{labels[0]}`"
        return "**" + "\n".join([f"- {c}" for c in labels]) + "**"

    @staticmethod
    def _format_weighted_choice(proposal: Proposal, choice: WeightedChoice) -> str:
        labels = {Snapshot._label_choice(proposal, int(c)): w for c, w in choice.items()}
        total_weight = sum(labels.values())
        choice_perc = [(c, round(100 * w / total_weight)) for c, w in labels.items()]
        choice_perc.sort(key=lambda x: x[1], reverse=True)
        graph = tpl.figure()
        graph.barh(
            [x[1] for x in choice_perc],
            [x[0] for x in choice_perc],
            force_ascii=True,
            max_width=15
        )
        return "```" + graph.get_string().replace("]", "%]") + "```"

    @staticmethod
    def draw_proposal(
            draw: BetterImageDraw,
            proposal: Proposal,
            width: int,
            x_offset: int = 0,
            y_offset: int = 0
    ) -> int:
        default_margin = 10
        pb_margin_left = 10
        pb_margin_right = 20
        perc_margin_left = 50

        def draw_choice(
                _proposal: Snapshot.Proposal,
                _choice: str,
                _score: float,
                _x_offset: int,
                _y_offset: int
        ) -> int:
            def safe_div(x, y):
                return (x / y) if y else 0

            color = {
                "for": (12, 181, 53),
                "against": (222, 4, 5)
            }.get(_choice.lower(), (255, 255, 255))
            max_score = max(proposal["scores"])

            choice_height = 0

            # {choice}
            draw.dynamic_text(
                (_x_offset + default_margin, _y_offset),
                _choice,
                font_size,
                max_width=(width / 2),
                anchor="lt"
            )
            # {choice}                           {score} votes
            draw.dynamic_text(
                (_x_offset + width - pb_margin_right, _y_offset),
                f"{_score:,.2f} votes",
                font_size,
                max_width=(width / 2),
                anchor="rt"
            )
            choice_height += 20
            # {choice}                           {score} votes
            #   {perc}%
            draw.dynamic_text(
                (_x_offset + perc_margin_left, _y_offset + choice_height),
                f"{safe_div(_score, proposal['scores_total']):.0%}",
                font_size,
                max_width=(width / 2) - perc_margin_left,
                anchor="rt"
            )
            # {choice}                           {score} votes
            #   {perc}% ======================================
            draw.progress_bar(
                (_x_offset + perc_margin_left + pb_margin_left, _y_offset + choice_height),
                (10, width - perc_margin_left - pb_margin_left - pb_margin_right - 10),
                safe_div(_score, max_score),
                primary=color
            )
            choice_height += 20
            return choice_height

        font_size = 15
        proposal_height = 0

        draw.dynamic_text(
            (x_offset + default_margin, y_offset),
            proposal["title"],
            25,
            max_width=(width - 2 * default_margin)
        )
        proposal_height += 40

        # order (choice, score) pairs by score
        choice_scores = list(zip(proposal["choices"], proposal["scores"]))
        choice_scores.sort(key=lambda x: x[1], reverse=True)
        for choice, score in choice_scores:
            proposal_height += draw_choice(proposal, choice, score, x_offset, y_offset + proposal_height)

        proposal_height += 10

        # quorum header
        draw.dynamic_text(
            (x_offset + default_margin, y_offset + proposal_height),
            "Quorum:",
            20,
            max_width=(width - 2 * default_margin)
        )
        proposal_height += 30

        # quorum progress bar
        quorum_perc: float = proposal["scores_total"] / proposal["quorum"]
        draw.dynamic_text(
            (x_offset + perc_margin_left, y_offset + proposal_height),
            f"{quorum_perc:.0%}",
            font_size,
            max_width=(width / 2) - perc_margin_left,
            anchor="rt"
        )
        pb_color = (242, 110, 52) if (quorum_perc >= 1) else (82, 81, 80)
        draw.progress_bar(
            (x_offset + perc_margin_left + pb_margin_left, y_offset + proposal_height),
            (10, width - perc_margin_left - pb_margin_left - pb_margin_right),
            min(quorum_perc, 1),
            primary=pb_color
        )
        proposal_height += 30

        # show remaining time until the vote ends
        rem_time = proposal["end"] - datetime.now().timestamp()
        time_label_width = (width - 2 * default_margin)
        draw.dynamic_text(
            (x_offset + time_label_width / 2, y_offset + proposal_height),
            f"{uptime(rem_time)} left" if (rem_time >= 0) else "Final result",
            font_size,
            max_width=time_label_width,
            anchor="mt"
        )
        proposal_height += 20
        return proposal_height

    @hybrid_command()
    async def snapshot_votes(self, ctx: Context):
        """
        Show currently active Snapshot votes.
        """
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        embed = Embed()
        embed.set_author(name="🔗 Data from snapshot.org", url="https://vote.rocketpool.net")

        proposals = self.get_proposals("active")
        if not proposals:
            embed.description = "No active proposals."
            return await ctx.send(embed=embed)

        num_proposals = len(proposals)
        num_cols = min(int(math.ceil(math.sqrt(num_proposals))), 4)
        num_rows = int(math.ceil(num_proposals / num_cols))

        v_spacing = 40
        h_spacing = 40
        proposal_width = 500

        total_height = v_spacing * (num_rows - 1)
        total_width = proposal_width * num_cols + h_spacing * (num_cols - 1)

        proposal_grid: list[list[Snapshot.Proposal]] = []
        for row_idx in range(num_rows):
            row = proposals[row_idx*num_cols:(row_idx+1)*num_cols]
            proposal_grid.append(row)

            # row height depends on number of proposal choices
            max_choices = max(len(p["choices"]) for p in row)
            total_height += 130 + 40 * max_choices

        # match Discord dark mode Embed color (#2b2d31)
        img = Image.new("RGB", (total_width, total_height), color=(43, 45, 49))
        draw = BetterImageDraw(img)

        # keeping track of widest row
        max_x_offset = 0

        # draw proposals in num_rows x num_cols grid
        y_offset = -h_spacing
        for row_idx in range(len(proposal_grid)):
            x_offset = -h_spacing
            y_offset += v_spacing

            max_height = 0
            for col_idx in range(len(proposal_grid[row_idx])):
                proposal = proposal_grid[row_idx][col_idx]
                x_offset += h_spacing
                height = self.draw_proposal(draw, proposal, proposal_width, x_offset, y_offset)
                max_height = max(max_height, height)
                x_offset += proposal_width

            y_offset += max_height
            max_x_offset = max(max_x_offset, x_offset)

        # y_offset monotonically increases
        max_y_offset = y_offset
        # make sure the image has the right dimensions
        assert(max_x_offset == total_width)
        assert(max_y_offset == total_height)

        # write drawn image to buffer
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        embed.set_image(url="attachment://votes.png")

        await ctx.send(embed=embed, file=File(buffer, "votes.png"))


async def setup(bot):
    await bot.add_cog(Snapshot(bot))
