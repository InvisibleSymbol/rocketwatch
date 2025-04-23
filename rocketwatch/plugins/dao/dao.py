import logging

from dataclasses import dataclass
from typing import Literal
from operator import attrgetter

from eth_typing import ChecksumAddress
from tabulate import tabulate

from discord import Interaction
from discord.app_commands import Choice, command, describe, autocomplete
from discord.ext.commands import Cog

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden, is_hidden_weak
from utils.dao import DefaultDAO, OracleDAO, SecurityCouncil, ProtocolDAO
from utils.views import PageView
from utils.embeds import el_explorer_url
from utils.event_logs import get_logs
from utils.block_time import ts_to_block
from utils.rocketpool import rp


log = logging.getLogger("dao")
log.setLevel(cfg["log_level"])


class OnchainDAO(Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    def get_dao_votes_embed(dao: DefaultDAO, full: bool) -> Embed:
        current_proposals: dict[DefaultDAO.ProposalState, list[DefaultDAO.Proposal]] = {
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
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=False, include_payload=full)}```"
                        f"Voting starts <t:{proposal.start}:R>, ends <t:{proposal.end}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=True, include_payload=full)}```"
                        f"Voting ends <t:{proposal.end}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.Active]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=full, include_payload=full)}```"
                        f"Expires <t:{proposal.expires}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @staticmethod
    def get_pdao_votes_embed(dao: ProtocolDAO, full: bool) -> Embed:
        current_proposals: dict[ProtocolDAO.ProposalState, list[ProtocolDAO.Proposal]] = {
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
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=False, include_payload=full)}```"
                        f"Voting starts <t:{proposal.start}:R>, ends <t:{proposal.end_phase_2}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.Pending]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active (Phase 1)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=True, include_payload=full)}```"
                        f"Next phase <t:{proposal.end_phase_1}:R>, voting ends <t:{proposal.end_phase_2}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase1]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Active (Phase 2)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=True, include_payload=full)}```"
                        f"Voting ends <t:{proposal.end_phase_2}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.ActivePhase2]
                ] + [
                    (
                        f"**Proposal #{proposal.id}** - Succeeded (Not Yet Executed)\n"
                        f"```{dao.build_proposal_body(proposal, include_proposer=full, include_votes=full, include_payload=full)}```"
                        f"Expires <t:{proposal.expires}:R>."
                    ) for proposal in current_proposals[dao.ProposalState.Succeeded]
                ]
            ) or "No active proposals."
        )

    @command()
    @describe(dao_name="DAO to show proposals for")
    @describe(full="show all information (e.g. payload)")
    async def dao_votes(
            self,
            interaction: Interaction,
            dao_name: Literal["oDAO", "pDAO", "Security Council"] = "pDAO",
            full: bool = False
    ) -> None:
        """Show currently active on-chain proposals"""
        visibility = is_hidden(interaction) if full else is_hidden_weak(interaction)
        await interaction.response.defer(ephemeral=visibility)

        match dao_name:
            case "pDAO":
                dao = ProtocolDAO()
                embed = self.get_pdao_votes_embed(dao, full)
            case "oDAO":
                dao = OracleDAO()
                embed = self.get_dao_votes_embed(dao, full)
            case "Security Council":
                dao = SecurityCouncil()
                embed = self.get_dao_votes_embed(dao, full)
            case _:
                raise ValueError(f"Invalid DAO name: {dao_name}")

        await interaction.followup.send(embed=embed)
        
    @dataclass(slots=True)
    class Vote:
        voter: ChecksumAddress
        direction: int
        voting_power: float
        time: int        

    class VoterPageView(PageView):
        def __init__(self, proposal: ProtocolDAO.Proposal):
            super().__init__(page_size=25)
            self.proposal = proposal
            self._voter_list = self._get_voter_list(proposal)
            
        def _get_voter_list(self, proposal: ProtocolDAO.Proposal) -> list['OnchainDAO.Vote']:            
            voters: dict[ChecksumAddress, OnchainDAO.Vote] = {}
            dao = ProtocolDAO()
                             
            for vote_log in get_logs(
                dao.proposal_contract.events.ProposalVoted,
                ts_to_block(proposal.start) - 1, 
                ts_to_block(proposal.end_phase_2) + 1,
                {"proposalID": proposal.id}
            ):
                vote = OnchainDAO.Vote(
                    vote_log.args.voter, 
                    vote_log.args.direction,
                    solidity.to_float(vote_log.args.votingPower),
                    vote_log.args.time
                )
                voters[vote.voter] = vote
                
            for override_log in get_logs(
                dao.proposal_contract.events.ProposalVoteOverridden,
                ts_to_block(proposal.end_phase_1) - 1,
                ts_to_block(proposal.end_phase_2) + 1,
                {"proposalID": proposal.id}
            ):
                voting_power = solidity.to_float(override_log.args.votingPower)
                voters[override_log.args.delegate].voting_power -= voting_power
                    
            return sorted(voters.values(), key=attrgetter("voting_power"), reverse=True)
            
        @property
        def _title(self) -> str:
            return f"pDAO Proposal #{self.proposal.id} - Voter List"
        
        async def _load_content(self, from_idx: int, to_idx: int) -> tuple[int, str]:            
            headers = ["#", "Voter", "Choice", "Weight"]
            data = []
            for i, voter in enumerate(self._voter_list[from_idx:(to_idx + 1)], start=from_idx):
                name = el_explorer_url(voter.voter, prefix=-1).split("[")[1].split("]")[0]
                vote = ["", "Abstain", "For", "Against", "Veto"][voter.direction]
                voting_power = f"{voter.voting_power:,.2f}"
                data.append([i+1, name, vote, voting_power])
                
            if not data:
                return 0, ""
            
            table = tabulate(data, headers, colalign=("right", "left", "left", "right"))
            return len(self._voter_list), f"```{table}```"
        
    async def _get_recent_proposals(self, interaction: Interaction, current: str) -> list[Choice[int]]:
        dao = ProtocolDAO()
        num_proposals = dao.proposal_contract.functions.getTotal().call()
        
        if current:
            try:
                suggestions = [int(current)]
                assert 1 <= int(current) <= num_proposals
            except (ValueError, AssertionError):
                return []
        else:
            suggestions = list(range(1, num_proposals + 1))[:-26:-1]
                    
        titles: list[str] = [
            res.results[0] for res in rp.multicall.aggregate([
                dao.proposal_contract.functions.getMessage(proposal_id) for proposal_id in suggestions
            ]).results
        ]
        return [Choice(name=f"#{pid}: {title}", value=pid) for pid, title in zip(suggestions, titles)]
        
    @command()
    @describe(proposal="proposal to show voters for")
    @autocomplete(proposal=_get_recent_proposals)
    async def voter_list(self, interaction: Interaction, proposal: int) -> None:
        """Show the list of voters for a pDAO proposal"""
        await interaction.response.defer(ephemeral=is_hidden_weak(interaction))
        if not (proposal := ProtocolDAO().fetch_proposal(proposal)):
            return await interaction.followup.send("Invalid proposal ID.")
        
        view = OnchainDAO.VoterPageView(proposal)
        embed = await view.load()
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(OnchainDAO(bot))
