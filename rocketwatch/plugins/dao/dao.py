import logging
import math

from enum import IntEnum
from typing import Literal

import termplotlib as tpl
from discord.ext.commands import Cog, Context, hybrid_command

from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.rocketpool import rp


log = logging.getLogger("snapshot")
log.setLevel(cfg["log_level"])


class DefaultDAO:
    def __init__(self, name: Literal["odao", "security council"]):
        if name == "odao":
            self.display_name = "oDAO"
            self.contract_name = "rocketDAONodeTrustedProposals"
        elif name == "security council":
            self.display_name = "Security Council"
            self.contract_name = "rocketDAOSecurityProposals"
        else:
            raise ValueError("Unknown DAO")

    class ProposalState(IntEnum):
        Pending = 0
        Active = 1
        Cancelled = 2
        Defeated = 3
        Succeeded = 4
        Expired = 5
        Executed = 6

    def get_votes(self):
        current_proposals: dict[DefaultDAO.ProposalState, list[dict]] = {
            self.ProposalState.Pending: [],
            self.ProposalState.Active: [],
            self.ProposalState.Succeeded: [],
        }

        num_proposals = rp.call("rocketDAOProposal.getTotal")
        for proposal_id in range(1, num_proposals + 1):
            def call(func: str):
                return rp.call(f"rocketDAOProposal.{func}", proposal_id)

            if call("getDAO") != self.contract_name:
                continue

            if (state := call("getState")) not in current_proposals:
                continue

            current_proposals[state].append({
                "id": proposal_id,
                "proposer": call("getProposer"),
                "message": call("getMessage"),
                "created": call("getCreated"),
                "start": call("getStart"),
                "end": call("getEnd"),
                "expires": call("getExpires"),
                "votes_for": solidity.to_int(call("getVotesFor")),
                "votes_against": solidity.to_int(call("getVotesAgainst")),
                "votes_required": math.ceil(solidity.to_float(call("getVotesRequired")))
            })

        def build_graph(_proposal: dict) -> str:
            graph = tpl.figure()
            graph.barh(
                [_proposal["votes_for"], _proposal["votes_against"], _proposal["votes_required"]],
                ["For", "Against", "Required"],
                max_width=20
            )
            return graph.get_string()

        e = Embed()
        e.title = f"{self.display_name} Proposals"
        e.description = "\n\n".join(
            [
                (
                    f"**Proposal #{proposal['id']}** - Pending\n"
                    f"Starts <t:{proposal['start']}:R>, ends <t:{proposal['end']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`"
                ) for proposal in current_proposals[self.ProposalState.Pending]
            ]
            +
            [
                (
                    f"**Proposal #{proposal['id']}** - Active\n"
                    f"Ends <t:{proposal['end']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`\n"
                    f"{build_graph(proposal)}"
                ) for proposal in current_proposals[self.ProposalState.Active]
            ]
            +
            [
                (
                    f"**Proposal #{proposal['id']}** - Succeeded (Not Yet Executed)\n"
                    f"Expires <t:{proposal['expires']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`\n"
                    f"{build_graph(proposal)}"
                ) for proposal in current_proposals[self.ProposalState.Succeeded]
            ]
        ) or "No active proposals."
        return e


class ProtocolDAO:
    class ProposalState(IntEnum):
        Pending = 0
        ActivePhase1 = 1
        ActivePhase2 = 2
        Destroyed = 3
        Vetoed = 4
        QuorumNotMet = 5
        Defeated = 6
        Succeeded = 7
        Expired = 8
        Executed = 9

    def get_votes(self):
        current_proposals: dict[ProtocolDAO.ProposalState, list[dict]] = {
            self.ProposalState.Pending: [],
            self.ProposalState.ActivePhase1: [],
            self.ProposalState.ActivePhase2: [],
            self.ProposalState.Succeeded: [],
        }

        num_proposals = rp.call("rocketDAOProtocolProposal.getTotal")
        for proposal_id in range(1, num_proposals + 1):
            def call(func: str):
                return rp.call(f"rocketDAOProtocolProposal.{func}", proposal_id)

            if (state := call("getState")) not in current_proposals:
                continue

            current_proposals[state].append({
                "id": proposal_id,
                "proposer": call("getProposer"),
                "message": call("getMessage"),
                "created": call("getCreated"),
                "start": call("getStart"),
                "end_phase1": call("getPhase1End"),
                "end_phase2": call("getPhase2End"),
                "expires": call("getExpires"),
                "votes_for": solidity.to_int(call("getVotingPowerFor")),
                "votes_against": solidity.to_int(call("getVotingPowerAgainst")),
                "votes_veto": solidity.to_int(call("getVotingPowerVeto")),
                "votes_abstain": solidity.to_int(call("getVotingPowerAbstained")),
                "quorum": math.ceil(solidity.to_float(call("getVotingPowerRequired"))),
                "veto_quorum": math.ceil(solidity.to_float(call("getVetoQuorum"))),
            })

        def build_graph(_proposal: dict) -> str:
            graph = tpl.figure()
            graph.barh(
                [
                    _proposal["votes_for"],
                    _proposal["votes_against"],
                    _proposal["votes_veto"],
                    _proposal["votes_abstain"],
                    _proposal["votes_for"] + _proposal["votes_against"] + _proposal["votes_abstain"],
                    _proposal["veto_quorum"],
                    _proposal["quorum"]
                ],
                ["For", "Against", "Veto", "Abstain", "Total", "Veto Quorum", "Quorum"],
                max_width=20
            )
            return graph.get_string()

        e = Embed()
        e.title = f"pDAO Proposals"
        e.description = "\n\n".join(
            [
                (
                    f"**Proposal #{proposal['id']}** - Pending\n"
                    f"Starts <t:{proposal['start']}:R>, ends <t:{proposal['end_phase2']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`"
                ) for proposal in current_proposals[self.ProposalState.Pending]
            ]
            +
            [
                (
                    f"**Proposal #{proposal['id']}** - Active (Phase 1)\n"
                    f"Next phase <t:{proposal['end_phase1']}:R>, ends <t:{proposal['end_phase2']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`\n"
                    f"{build_graph(proposal)}"
                ) for proposal in current_proposals[self.ProposalState.ActivePhase1]
            ]
            +
            [
                (
                    f"**Proposal #{proposal['id']}** - Active (Phase 2)\n"
                    f"Ends <t:{proposal['end']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`\n"
                    f"{build_graph(proposal)}"
                ) for proposal in current_proposals[self.ProposalState.ActivePhase2]
            ]
            +
            [
                (
                    f"**Proposal #{proposal['id']}** - Succeeded (Not Yet Executed)\n"
                    f"Expires <t:{proposal['expires']}:R>\n"
                    f"Message:  `{DAO.sanitize(proposal['message'])}`\n"
                    f"Proposer: `{proposal['proposer']}`\n"
                    f"{build_graph(proposal)}"
                ) for proposal in current_proposals[self.ProposalState.Succeeded]
            ]
        ) or "No active proposals."
        return e


class DAO(Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def sanitize(message: str) -> str:
        max_length = 40
        suffix = "..."
        if len(message) > max_length:
            message = message[:max_length - len(suffix)] + suffix
        return message

    @hybrid_command()
    async def dao_votes(
            self,
            ctx: Context,
            dao_name: Literal["odao", "pdao", "security council"] = "pdao"
    ):
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        dao = ProtocolDAO() if dao_name == "pdao" else DefaultDAO(dao_name)
        embed = dao.get_votes()
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DAO(bot))
