import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Literal

import numpy as np
import pymongo
import requests
import termplotlib as tpl
from PIL.Image import Image
from discord.ext.commands import Context, hybrid_command
from eth_typing import ChecksumAddress
from graphql_query import Operation, Query, Argument
from web3.constants import ADDRESS_ZERO

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed, el_explorer_url
from utils.image import ImageCanvas
from utils.readable import uptime
from utils.rocketpool import rp
from utils.shared_w3 import w3
from utils.event import EventSubmodule, Event
from utils.visibility import is_hidden_weak

log = logging.getLogger("snapshot")
log.setLevel(cfg["log_level"])


class Snapshot(EventSubmodule):
    def __init__(self, bot: RocketWatch):
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

    @dataclass(frozen=True)
    class Proposal:
        State = Literal["active", "closed"]

        id: str
        title: str
        choices: list[str]
        start: int
        end: int
        scores: list[float]
        quorum: int

        _TEXT_SIZE = 15
        _HEADER_SIZE = 20
        _TITLE_SIZE = 25
        _PB_SIZE = 20

        _V_SPACE_SMALL = 5
        _V_SPACE_MEDIUM = 10
        _V_SPACE_LARGE = 15

        def predict_render_height(self, with_title: bool = True) -> int:
            height = 0
            if with_title:
                height = self._TITLE_SIZE + self._V_SPACE_LARGE
            height += len(self.choices) * (self._TEXT_SIZE + self._V_SPACE_SMALL + self._PB_SIZE)
            height += self._V_SPACE_MEDIUM + self._HEADER_SIZE
            height += self._V_SPACE_MEDIUM + self._PB_SIZE
            height += self._V_SPACE_MEDIUM + self._TEXT_SIZE
            return height

        def render_to(
                self,
                canvas: ImageCanvas,
                width: int,
                x_offset: int = 0,
                y_offset: int = 0,
                *,
                include_title: bool = True
        ) -> int:
            default_margin = 10
            pb_margin_left = 10
            pb_margin_right = 20
            perc_margin_left = 50

            def safe_div(x, y):
                return (x / y) if y else 0

            def render_choice(_choice: str, _score: float, _x_offset: int, _y_offset: int) -> int:
                color = {
                    "for": (12, 181, 53),      # green
                    "against": (222, 4, 5),    # red
                    "abstain": (114, 121, 138) # slate gray
                }.get(_choice.lower(), (192, 192, 192))
                max_score = max(self.scores)

                choice_height = 0

                # {choice}
                canvas.dynamic_text(
                    (_x_offset + default_margin, _y_offset),
                    _choice,
                    self._TEXT_SIZE,
                    max_width=(width // 2),
                    anchor="lt"
                )
                # {choice}                                 {score}
                canvas.dynamic_text(
                    (_x_offset + width - pb_margin_right, _y_offset),
                    f"{_score:,.2f}",
                    self._TEXT_SIZE,
                    max_width=(width // 2),
                    anchor="rt"
                )
                choice_height += self._TEXT_SIZE + self._V_SPACE_SMALL
                # {choice}                                 {score}
                #   {perc}%
                canvas.dynamic_text(
                    (_x_offset + perc_margin_left, _y_offset + choice_height),
                    f"{safe_div(_score, sum(self.scores)):.0%}",
                    self._TEXT_SIZE,
                    max_width=(width // 2) - perc_margin_left,
                    anchor="rt"
                )
                # {choice}                                 {score}
                #   {perc}% ======================================
                canvas.progress_bar(
                    (_x_offset + perc_margin_left + pb_margin_left, _y_offset + choice_height),
                    (self._PB_SIZE // 2, width - perc_margin_left - pb_margin_left - pb_margin_right - 10),
                    safe_div(_score, max_score),
                    primary=color
                )
                choice_height += self._PB_SIZE
                return choice_height

            proposal_height = 0

            if include_title:
                canvas.dynamic_text(
                    (x_offset + default_margin, y_offset),
                    self.title,
                    self._TITLE_SIZE,
                    max_width=(width - 2 * default_margin)
                )
                proposal_height += self._TITLE_SIZE + self._V_SPACE_LARGE

            # order (choice, score) pairs by score
            choice_scores = list(zip(self.choices, self.scores))
            choice_scores.sort(key=lambda x: x[1], reverse=True)
            for choice, score in choice_scores:
                proposal_height += render_choice(choice, score, x_offset, y_offset + proposal_height)

            proposal_height += self._V_SPACE_MEDIUM

            # quorum header
            canvas.dynamic_text(
                (x_offset + default_margin, y_offset + proposal_height),
                "Quorum:",
                self._HEADER_SIZE,
                max_width=(width - 2 * default_margin)
            )
            proposal_height += self._HEADER_SIZE + self._V_SPACE_MEDIUM

            # quorum progress bar
            quorum_perc: float = safe_div(sum(self.scores), self.quorum)
            canvas.dynamic_text(
                (x_offset + perc_margin_left, y_offset + proposal_height),
                f"{quorum_perc:.0%}",
                self._TEXT_SIZE,
                max_width=(width // 2) - perc_margin_left,
                anchor="rt"
            )
            # dark gray, turns orange when quorum is met
            pb_color = (242, 110, 52) if (quorum_perc >= 1) else (82, 81, 80)
            canvas.progress_bar(
                (x_offset + perc_margin_left + pb_margin_left, y_offset + proposal_height),
                (self._PB_SIZE // 2, width - perc_margin_left - pb_margin_left - pb_margin_right - 10),
                min(quorum_perc, 1),
                primary=pb_color
            )
            proposal_height += self._PB_SIZE + self._V_SPACE_MEDIUM

            # show remaining time until the vote ends
            rem_time = self.end - datetime.now().timestamp()
            time_label_width = (width - 2 * default_margin)
            canvas.dynamic_text(
                (x_offset + time_label_width // 2, y_offset + proposal_height),
                f"{uptime(rem_time)} left" if (rem_time >= 0) else "Final Result",
                self._TEXT_SIZE,
                max_width=time_label_width,
                anchor="mt"
            )
            proposal_height += self._TEXT_SIZE
            return proposal_height

        def get_embed_template(self) -> Embed:
            embed = Embed()
            embed.set_author(
                name="🔗 Data from snapshot.org",
                url=f"https://vote.rocketpool.net/#/proposal/{self.id}"
            )
            return embed

        def create_image(self, *, include_title: bool) -> Image:
            height = self.predict_render_height(include_title)
            width = max(500, height)
            canvas = ImageCanvas(width, height)
            self.render_to(canvas, width, 0, 0, include_title=include_title)
            return canvas.image

        def create_start_event(self) -> Event:
            embed = self.get_embed_template()
            embed.title = ":bulb: New Snapshot Proposal"
            return Event(
                embed=embed,
                topic="snapshot",
                block_number=w3.eth.getBlock("latest").number,
                event_name="pdao_snapshot_vote_start",
                unique_id=f"{self.id}:event_start",
                attachment=self.create_image(include_title=True)
            )

        def create_end_event(self) -> Event:
            reached_quorum = sum(self.scores) >= self.quorum
            winning_choice = self.choices[np.argmax(self.scores)]

            embed = self.get_embed_template()
            if reached_quorum and ("against" not in winning_choice.lower()):
                # potentially fails if abstain > against > for
                embed.title = ":white_check_mark: Snapshot Proposal Passed"
            else:
                embed.title = ":x: Snapshot Proposal Failed"

            return Event(
                embed=embed,
                topic="snapshot",
                block_number=w3.eth.getBlock("latest").number,
                event_name="pdao_snapshot_vote_end",
                unique_id=f"{self.id}:event_end",
                attachment=self.create_image(include_title=True)
            )

    @dataclass(frozen=True)
    class MinimalVote:
        SingleChoice = int
        MultiChoice = list[SingleChoice]
        # weighted votes use strings as keys for some reason
        WeightedChoice = dict[str, int]
        Choice = (SingleChoice | MultiChoice | WeightedChoice)

        proposal: 'Snapshot.Proposal'
        voter: ChecksumAddress
        choice: Choice
        created: int

        def pretty_print(self) -> Optional[str]:
            match (raw_choice := self.choice):
                case int():
                    return self._format_single_choice(raw_choice)
                case list():
                    return self._format_multiple_choice(raw_choice)
                case dict():
                    return self._format_weighted_choice(raw_choice)
                case _:
                    log.error(f"Unknown vote type: {raw_choice}")
                    return None

        def _label_choice(self, raw_vote: SingleChoice) -> str:
            # vote choice represented as 1-based index
            return self.proposal.choices[raw_vote - 1]

        def _format_single_choice(self, choice: SingleChoice):
            label = self._label_choice(choice)
            match label.lower():
                case "for":
                    label = "✅ For"
                case "against":
                    label = "❌ Against"
                case "abstain":
                    label = "⚪ Abstain"
            return f"`{label}`"

        def _format_multiple_choice(self, choice: MultiChoice) -> str:
            labels = [self._label_choice(c) for c in choice]
            if len(labels) == 1:
                return f"`{labels[0]}`"
            return "**" + "\n".join([f"- {c}" for c in labels]) + "**"

        def _format_weighted_choice(self, choice: WeightedChoice) -> str:
            labels = {self._label_choice(int(c)): w for c, w in choice.items()}
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

    @dataclass(frozen=True)
    class Vote(MinimalVote):
        id: str
        vp: float
        reason: str

        def create_event(self, prev_vote: Optional['Snapshot.MinimalVote']) -> Optional[Event]:
            node = rp.call("rocketSignerRegistry.signerToNode", self.voter)
            signer = el_explorer_url(self.voter)
            voter = signer if (node == ADDRESS_ZERO) else el_explorer_url(node)

            vote_fmt = self.pretty_print()
            if vote_fmt is None:
                return None

            embed = self.proposal.get_embed_template()
            embed.title = f":ballot_box: {self.proposal.title}"

            if prev_vote is None:
                if len(vote_fmt) <= 20:
                    embed.description = f"{voter} voted {vote_fmt}"
                else:
                    embed.description = f"{voter} voted\n{vote_fmt}"
            else:
                assert prev_vote.proposal.id == self.proposal.id
                prev_vote_fmt = prev_vote.pretty_print()
                if len(vote_fmt) <= 10 and len(prev_vote_fmt) <= 10:
                    embed.description = f"{voter} changed their vote from {prev_vote_fmt} to {vote_fmt}"
                else:
                    embed.description = (
                        f"{voter} changed their vote from\n"
                        f"{prev_vote_fmt}\n"
                        f"to\n"
                        f"{vote_fmt}"
                    )

            if self.reason:
                max_length = 2000
                reason = self.reason
                if len(embed.description) + len(reason) > max_length:
                    suffix = "..."
                    overage = len(embed.description) + len(reason) - max_length
                    reason = reason[:-(overage + len(suffix))] + suffix

                embed.description += f" ```{reason}```"

            embed.add_field(name="Signer", value=signer)
            embed.add_field(name="Vote Power", value=f"{self.vp:,.2f}")
            embed.add_field(name="Timestamp", value=f"<t:{self.created}:R>")

            event_name = "pdao_snapshot_vote" if (self.vp >= 250) else "snapshot_vote"
            return Event(
                embed=embed,
                topic="snapshot",
                block_number=w3.eth.getBlock("latest").number,
                event_name=event_name,
                unique_id=f"{self.proposal.id}_{self.voter}_{self.created}:vote",
                attachment=self.proposal.create_image(include_title=False)
            )

    @staticmethod
    def fetch_proposals(
            state: Proposal.State,
            reverse: bool = False,
            limit: int = 25,
            proposal_id: Optional[str] = None
    ) -> list[Proposal]:
        proposal_filter = [
            Argument(name="space_in", value=["\"rocketpool-dao.eth\""]),
            Argument(name="state", value=f"\"{state}\"")
        ]
        if proposal_id:
            proposal_filter.append(Argument(name="id", value=f"\"{proposal_id}\""))

        query = Query(
            name="proposals",
            arguments=[
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(name="where", value=proposal_filter),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc" if reverse else "asc")
            ],
            fields=["id", "title", "choices", "start", "end", "scores", "quorum"]
        )
        response: list[dict] = Snapshot._query_api([query])["proposals"]
        return [Snapshot.Proposal(**d) for d in response]

    @staticmethod
    def fetch_votes(proposal: Proposal, reverse: bool = True, limit: int = 100) -> list[Vote]:
        query = Query(
            name="votes",
            arguments=[
                Argument(name="first", value=limit),
                Argument(name="skip", value=0),
                Argument(
                    name="where",
                    value=[Argument(name="proposal", value=f"\"{proposal.id}\"")]
                ),
                Argument(name="orderBy", value="\"created\""),
                Argument(name="orderDirection", value="desc" if reverse else "asc")
            ],
            fields=["id", "voter", "created", "vp", "choice", "reason"]
        )
        response: list[dict] = Snapshot._query_api([query])["votes"]
        return [Snapshot.Vote(**(d | {"proposal": proposal})) for d in response]

    def _get_new_events(self) -> list[Event]:
        if not self.db.snapshot_votes.find_one({"_id": "version", "version": self.version}):
            log.warning("Snapshot version changed, nuking db")
            self.db.snapshot_votes.drop()
            self.db.snapshot_votes.insert_one({"_id": "version", "version": self.version})

        now = datetime.now()
        events: list[Event] = []
        db_updates: list[dict] = []

        known_active_proposal_ids: set[str] = set()
        for stored_proposal in self.db.snapshot_proposals.find():
            if stored_proposal["end"] > now.timestamp():
                known_active_proposal_ids.add(stored_proposal["_id"])
            else:
                # stored proposal ended, emit event and delete from DB
                log.info(f"Found expired proposal: {stored_proposal}")
                # recover full proposal
                proposal = self.fetch_proposals("closed", proposal_id=stored_proposal["_id"])[0]
                event = proposal.create_end_event()
                self.db.snapshot_proposals.delete_one(stored_proposal)
                events.append(event)

        # fetch in descending order, then reverse to get latest results in chronological order
        # only relevant if potential results exceed limit
        active_proposals = self.fetch_proposals("active", reverse=True)[::-1]
        for proposal in active_proposals:
            log.debug(f"Processing proposal {proposal}")
            if proposal.id not in known_active_proposal_ids:
                # not aware of this proposal yet, emit event and insert into DB
                log.info(f"Found new proposal: {proposal}")
                event = proposal.create_start_event()
                self.db.snapshot_proposals.insert_one({
                    "_id"  : proposal.id,
                    "start": proposal.start,
                    "end"  : proposal.end
                })
                events.append(event)

            previous_votes: dict[ChecksumAddress, Snapshot.MinimalVote] = {}
            for stored_vote in self.db.snapshot_votes.find({"proposal_id": proposal.id}):
                vote = Snapshot.MinimalVote(
                    proposal=proposal,
                    voter=stored_vote["voter"],
                    choice=stored_vote["choice"],
                    created=stored_vote["timestamp"]
                )
                previous_votes[vote.voter] = vote

            current_votes = self.fetch_votes(proposal, reverse=True)[::-1]
            for vote in current_votes:
                log.debug(f"Processing vote {vote}")

                prev_vote: Optional[Snapshot.MinimalVote] = previous_votes.get(vote.voter)
                if prev_vote and (prev_vote.choice == vote.choice):
                    log.debug(f"Same vote choice as before, skipping event")
                    continue

                previous_votes[vote.voter] = vote

                event = vote.create_event(prev_vote)
                if event is None:
                    continue

                events.append(event)
                db_update = {
                    "proposal_id": proposal.id,
                    "voter"      : vote.voter,
                    "choice"     : vote.choice,
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

    @hybrid_command()
    async def snapshot_votes(self, ctx: Context):
        """Show currently active Snapshot votes."""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        embed = Embed(title="Snapshot Proposals")
        embed.set_author(name="🔗 Data from snapshot.org", url="https://vote.rocketpool.net")

        proposals = self.fetch_proposals("active", reverse=True)[::-1]
        if not proposals:
            embed.description = "No active proposals."
            return await ctx.send(embed=embed)

        num_proposals = len(proposals)
        num_cols = min(int(math.ceil(math.sqrt(num_proposals))), 4)
        num_rows = int(math.ceil(num_proposals / num_cols))

        v_spacing = 40
        h_spacing = 40

        # could potentially be smarter about arranging proposals with different proportions
        total_height = v_spacing * (num_rows - 1)
        proposal_grid: list[list[Snapshot.Proposal]] = []
        for row_idx in range(num_rows):
            row = proposals[row_idx*num_cols:(row_idx+1)*num_cols]
            proposal_grid.append(row)
            # row height is equal to height of its tallest proposal
            total_height += max(p.predict_render_height() for p in row)

        proposal_width = 500
        total_width = (proposal_width * num_cols) + h_spacing * (num_cols - 1)
        # make sure proportions don't become too skewed
        if total_width < total_height:
            proposal_width = (total_height - h_spacing * (num_cols - 1)) // num_cols
            total_width = (proposal_width * num_cols) + h_spacing * (num_cols - 1)

        canvas = ImageCanvas(total_width, total_height)

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
                height = proposal.render_to(canvas, proposal_width, x_offset, y_offset)
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
        file = canvas.image.to_file("votes.png")
        embed.set_image(url=f"attachment://{file.filename}")
        await ctx.send(embed=embed, file=file)


async def setup(bot):
    await bot.add_cog(Snapshot(bot))
