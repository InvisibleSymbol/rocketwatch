import logging
from datetime import datetime

from discord.ext.commands import Context, hybrid_command
from eth_typing import HexStr
from web3.constants import HASH_ZERO

from plugins.snapshot.snapshot import Snapshot
from plugins.forum.forum import Forum
from utils.status import StatusPlugin
from utils.cfg import cfg
from utils.dao import DAO, DefaultDAO, ProtocolDAO
from utils.embeds import Embed
from utils.visibility import is_hidden_weak
from utils.get_nearest_block import get_block_by_timestamp


log = logging.getLogger("governance")
log.setLevel(cfg["log_level"])


class Governance(StatusPlugin):
    @staticmethod
    def _get_active_pdao_proposals(dao: ProtocolDAO) -> list[ProtocolDAO.Proposal]:
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = []
        active_proposal_ids += proposals[dao.ProposalState.ActivePhase1]
        active_proposal_ids += proposals[dao.ProposalState.ActivePhase2]
        return [dao.fetch_proposal(proposal_id) for proposal_id in active_proposal_ids]

    @staticmethod
    def _get_active_dao_proposals(dao: DefaultDAO) -> list[DefaultDAO.Proposal]:
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = proposals[dao.ProposalState.Active]
        return [dao.fetch_proposal(proposal_id) for proposal_id in active_proposal_ids]

    @staticmethod
    def _get_tx_hash_for_proposal(dao: DAO, proposal: DAO.Proposal) -> HexStr:
        from_block = get_block_by_timestamp(proposal.created)[0] - 1
        to_block = get_block_by_timestamp(proposal.created)[0] + 1

        log.info(f"Looking for proposal {proposal} in [{from_block},{to_block}]")
        for receipt in dao.proposal_contract.events.ProposalAdded().get_logs(fromBlock=from_block, toBlock=to_block):
            log.info(f"Found receipt {receipt}")
            if receipt.args.proposalID == proposal.id:
                return receipt.transactionHash.hex()

        return HASH_ZERO

    @staticmethod
    def _get_active_snapshot_proposals() -> list[Snapshot.Proposal]:
        return Snapshot.fetch_proposals("active")

    @staticmethod
    async def _get_latest_forum_topics() -> list[Forum.Topic]:
        topics = await Forum.get_recent_topics()
        now = datetime.now().timestamp()
        # only get topics from within a week
        topics = [t for t in topics if (now - t.last_post_at) <= (7 * 24 * 60 * 60)]
        return topics

    @staticmethod
    async def get_digest() -> Embed:
        embed = Embed(title="Governance Digest", description="")

        # --------- PROTOCOL DAO --------- #

        embed.description += "### Protocol DAO\n"

        dao = ProtocolDAO()
        if proposals := Governance._get_active_pdao_proposals(dao):
            embed.description = "- **Active on-chain proposals**\n"
            for i, proposal in enumerate(proposals):
                title = DAO.sanitize(proposal.message)
                tx_hash = Governance._get_tx_hash_for_proposal(dao, proposal)
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i+1}. [{title}]({url})\n"
        else:
            embed.description += f"- **No active on-chain proposals**\n"

        if snapshot_proposals := Governance._get_active_snapshot_proposals():
            embed.description += "- **Active Snapshot proposals**\n"
            for i, proposal in enumerate(snapshot_proposals, start=1):
                title = DAO.sanitize(proposal.title)
                embed.description += f"  {i}. [{title}]({proposal.url})\n"
        else:
            embed.description += "- **No active Snapshot proposals**\n"

        # --------- ORACLE DAO --------- #

        embed.description += "### Oracle DAO\n"

        dao = DefaultDAO("rocketDAONodeTrustedProposals")
        if proposals := Governance._get_active_dao_proposals(dao):
            embed.description += "- **Active proposals**\n"
            for i, proposal in enumerate(proposals, start=1):
                title = DAO.sanitize(proposal.message)
                tx_hash = Governance._get_tx_hash_for_proposal(dao, proposal)
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i}. [{title}]({url})\n"
        else:
            embed.description += "- **No active proposals**\n"

        # --------- SECURITY COUNCIL --------- #

        embed.description += "### Security Council\n"

        dao = DefaultDAO("rocketDAOSecurityProposals")
        if proposals := Governance._get_active_dao_proposals(dao):
            embed.description += "- **Active proposals**\n"
            for i, proposal in enumerate(proposals, start=1):
                title = DAO.sanitize(proposal.message)
                tx_hash = Governance._get_tx_hash_for_proposal(DefaultDAO("rocketDAOSecurityProposals"), proposal)
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i}. [{title}]({url})\n"
        else:
            embed.description += "- **No active proposals**\n"

        # --------- DAO FORUM --------- #

        embed.description += "### Forum\n"

        if topics := await Governance._get_latest_forum_topics():
            embed.description += "- **Recently active topics**\n"
            for i, topic in enumerate(topics[:10], start=1):
                title = DAO.sanitize(topic.title)
                embed.description += f"  {i}. [{title}]({topic.url})\n"
        else:
            embed.description += "- **No recently active topics**\n"

        return embed

    @hybrid_command()
    async def governance_digest(self, ctx: Context) -> None:
        """Get a summary of current activity in protocol governance"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = await self.get_digest()
        await ctx.send(embed=embed)

    @staticmethod
    async def get_status_message() -> Embed:
        embed = await Governance.get_digest()
        embed.title = ":classical_building: Live Governance Digest"
        return embed


async def setup(bot):
    await bot.add_cog(Governance(bot))
