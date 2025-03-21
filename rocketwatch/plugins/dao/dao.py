import logging

from typing import Literal

from discord.app_commands import describe
from discord.ext.commands import Cog, Context, hybrid_command

from rocketwatch import RocketWatch
from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden, is_hidden_weak
from utils.dao import DefaultDAO, ProtocolDAO


log = logging.getLogger("dao")
log.setLevel(cfg["log_level"])


class DAOCommand(Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    def get_dao_votes_embed(dao: DefaultDAO, full: bool) -> Embed:
        current_proposals: dict[dao.ProposalState, list[dao.Proposal]] = {
            dao.ProposalState.Pending: [],
            dao.ProposalState.Active: [],
            dao.ProposalState.Succeeded: [],
        }

        for state, ids in dao.get_proposals_by_state().items():
            if state in current_proposals:
                current_proposals[state].extend([dao.fetch_proposal(pid) for pid in ids])

        return Embed(
            title=f"{dao.display_name} Proposals",
            description="\n\n".join(
                [
                    (
                        f"**Proposal #{proposal.id}** - Pending\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=False)}```"
                        f"Starts <t:{proposal.start}:R>, ends <t:{proposal.end}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_payload=full)}```"
                        f"Ends <t:{proposal.end}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Active]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_payload=full)}```"
                        f"Expires <t:{proposal.expires}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @staticmethod
    def get_pdao_votes_embed(dao: ProtocolDAO, full: bool) -> Embed:
        current_proposals: dict[dao.ProposalState, list[dao.Proposal]] = {
            dao.ProposalState.Pending: [],
            dao.ProposalState.ActivePhase1: [],
            dao.ProposalState.ActivePhase2: [],
            dao.ProposalState.Succeeded: [],
        }

        for state, ids in dao.get_proposals_by_state().items():
            if state in current_proposals:
                current_proposals[state].extend([dao.fetch_proposal(pid) for pid in ids])

        return Embed(
            title="pDAO Proposals",
            description="\n\n".join(
                [
                    (
                        f"**Proposal #{proposal.id}** - Pending\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=False)}```"
                        f"Starts <t:{proposal.start}:R>, ends <t:{proposal.end_phase_2}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active (Phase 1)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_payload=full)}```"
                        f"Next phase <t:{proposal.end_phase_1}:R>, voting ends <t:{proposal.end_phase_2}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase1]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active (Phase 2)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_payload=full)}```"
                        f"Ends <t:{proposal.end_phase_2}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase2]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_payload=full)}```"
                        f"Expires <t:{proposal.expires}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @hybrid_command()
    @describe(dao_name="DAO to show proposals for")
    @describe(full="show all information (e.g. payload)")
    async def dao_votes(
            self,
            ctx: Context,
            dao_name: Literal["oDAO", "pDAO", "Security Council"] = "pDAO",
            full: bool = False
    ) -> None:
        """
        Show currently active on-chain votes
        """
        await ctx.defer(ephemeral=is_hidden(ctx) if full else is_hidden_weak(ctx))

        match dao_name:
            case "pDAO":
                dao = ProtocolDAO()
                embed = self.get_pdao_votes_embed(dao, full)
            case "oDAO":
                dao = DefaultDAO("rocketDAONodeTrustedProposals")
                embed = self.get_dao_votes_embed(dao, full)
            case "Security Council":
                dao = DefaultDAO("rocketDAOSecurityProposals")
                embed = self.get_dao_votes_embed(dao, full)
            case _:
                raise ValueError(f"Invalid DAO name: {dao_name}")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DAOCommand(bot))
