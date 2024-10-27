import logging

from typing import Literal

from discord.app_commands import describe
from discord.ext.commands import Cog, Context, hybrid_command

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden, is_hidden_weak
from utils.rocketpool import rp
from utils.dao import DefaultDAO, ProtocolDAO


log = logging.getLogger("dao")
log.setLevel(cfg["log_level"])


class DAOCommand(Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def get_dao_votes_embed(dao: DefaultDAO, full: bool) -> Embed:
        current_proposals: dict[DefaultDAO.ProposalState, list[dict]] = {
            dao.ProposalState.Pending: [],
            dao.ProposalState.Active: [],
            dao.ProposalState.Succeeded: [],
        }

        num_proposals = rp.call("rocketDAOProposal.getTotal")
        for proposal_id in range(1, num_proposals + 1):
            state = rp.call("rocketDAOProposal.getState", proposal_id)
            if state not in current_proposals:
                continue

            if rp.call("rocketDAOProposal.getDAO", proposal_id) != dao.contract_name:
                continue

            proposal = dao.fetch_proposal(proposal_id)
            current_proposals[state].append(proposal)

        return Embed(
            title=f"{dao.display_name} Proposals",
            description="\n\n".join(
                [
                    (
                        f"**Proposal #{proposal['id']}** - Pending\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full, include_votes=False)}```"
                        f"Starts <t:{proposal['start']}:R>, ends <t:{proposal['end']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal['id']}** - Active\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full)}```"
                        f"Ends <t:{proposal['end']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Active]
                ] + [
                    (
                        f"**Proposal #{proposal['id']}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full)}```"
                        f"Expires <t:{proposal['expires']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @staticmethod
    def get_pdao_votes_embed(dao: ProtocolDAO, full: bool) -> Embed:
        current_proposals: dict[ProtocolDAO.ProposalState, list[dict]] = {
            dao.ProposalState.Pending: [],
            dao.ProposalState.ActivePhase1: [],
            dao.ProposalState.ActivePhase2: [],
            dao.ProposalState.Succeeded: [],
        }

        num_proposals = rp.call("rocketDAOProtocolProposal.getTotal")
        for proposal_id in range(1, num_proposals + 1):
            state = rp.call("rocketDAOProtocolProposal.getState", proposal_id)
            if state not in current_proposals:
                continue

            proposal = dao.fetch_proposal(proposal_id)
            current_proposals[state].append(proposal)

        return Embed(
            title="pDAO Proposals",
            description="\n\n".join(
                [
                    (
                        f"**Proposal #{proposal['id']}** - Pending\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full, include_votes=False)}```"
                        f"Starts <t:{proposal['start']}:R>, ends <t:{proposal['end_phase2']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal['id']}** - Active (Phase 1)\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full)}```"
                        f"Next phase <t:{proposal['end_phase1']}:R>, voting ends <t:{proposal['end_phase2']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase1]
                ] + [
                    (
                        f"**Proposal #{proposal['id']}** - Active (Phase 2)\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full)}```"
                        f"Ends <t:{proposal['end_phase2']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase2]
                ] + [
                    (
                        f"**Proposal #{proposal['id']}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_payload=full)}```"
                        f"Expires <t:{proposal['expires']}:R>"
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @hybrid_command()
    @describe(dao_name="DAO to show votes for")
    @describe(full="show all information (e.g. payload)")
    async def dao_votes(
            self,
            ctx: Context,
            dao_name: Literal["oDAO", "pDAO", "Security Council"] = "pdao",
            full: bool = False
    ):
        """
        Show currently active onchain votes.
        """
        await ctx.defer(ephemeral=is_hidden(ctx) if full else is_hidden_weak(ctx))

        if dao_name == "pDAO":
            dao = ProtocolDAO()
            embed = self.get_pdao_votes_embed(dao, full)
        else:
            dao = DefaultDAO({
                "oDAO": "rocketDAONodeTrustedProposals",
                "Security Council": "rocketDAOSecurityProposals"
            }[dao_name])
            embed = self.get_dao_votes_embed(dao, full)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DAOCommand(bot))