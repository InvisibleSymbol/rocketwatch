import logging
from datetime import datetime

from discord.ext import commands
from discord.ext.commands import Context, hybrid_command

from rocketwatch import RocketWatch
from plugins.snapshot.snapshot import Snapshot
from plugins.forum.forum import Forum
from utils.cfg import cfg
from utils.dao import DAO, DefaultDAO, ProtocolDAO
from utils.embeds import Embed
from utils.visibility import is_hidden_weak

log = logging.getLogger("governance")
log.setLevel(cfg["log_level"])


class Governance(commands.Cog):
    def __init__(self, bot: RocketWatch):
        self.bot = bot

    @staticmethod
    def _get_active_pdao_proposals() -> list[ProtocolDAO.Proposal]:
        dao = ProtocolDAO()
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = []
        active_proposal_ids += proposals[dao.ProposalState.ActivePhase1]
        active_proposal_ids += proposals[dao.ProposalState.ActivePhase2]
        return [dao.fetch_proposal(proposal_id) for proposal_id in active_proposal_ids]

    @staticmethod
    def _get_active_odao_proposals() -> list[DefaultDAO.Proposal]:
        dao = DefaultDAO("rocketDAONodeTrustedProposals")
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = proposals[dao.ProposalState.Active]
        return [dao.fetch_proposal(proposal_id) for proposal_id in active_proposal_ids]

    @staticmethod
    def _get_active_security_proposals() -> list[DefaultDAO.Proposal]:
        dao = DefaultDAO("rocketDAOSecurityProposals")
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = proposals[dao.ProposalState.Active]
        return [dao.fetch_proposal(proposal_id) for proposal_id in active_proposal_ids]

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

    @hybrid_command()
    async def governance_digest(self, ctx: Context) -> None:
        """Get a summary of protocol governance activities"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))

        embed = Embed(title="Governance Digest", description="")

        # --------- PROTOCOL DAO --------- #

        embed.description += "### Protocol DAO\n"
        proposals = self._get_active_pdao_proposals()
        if proposals:
            embed.description = "- **Active on-chain proposals**\n"
            for i, proposal in enumerate(proposals):
                title = DAO.sanitize(proposal.message)
                tx_hash = "0x94d1bb675e2278e221daabc1e3f2564bc33fa2ce01f29e94e0165fbcd46e654f" # TODO
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i+1}. [{title}]({url})\n"
        else:
            embed.description += f"- **No active on-chain proposals**\n"

        snapshot_proposals = self._get_active_snapshot_proposals()
        if snapshot_proposals:
            embed.description += "- **Active Snapshot proposals**\n"
            for i, proposal in enumerate(snapshot_proposals, start=1):
                title = DAO.sanitize(proposal.title)
                embed.description += f"  {i}. [{title}]({proposal.url})\n"
        else:
            embed.description += "- **No active Snapshot proposals**\n"

        # --------- ORACLE DAO --------- #

        embed.description += "### Oracle DAO\n"
        proposals = self._get_active_odao_proposals()
        if proposals:
            embed.description += "- **Active proposals**\n"
            for i, proposal in enumerate(proposals, start=1):
                title = DAO.sanitize(proposal.message)
                tx_hash = "0x94d1bb675e2278e221daabc1e3f2564bc33fa2ce01f29e94e0165fbcd46e654f" # TODO
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i}. [{title}]({url})\n"
        else:
            embed.description += "- **No active proposals**\n"

        # --------- SECURITY COUNCIL --------- #

        embed.description += "### Security Council\n"
        proposals = self._get_active_security_proposals()
        if proposals:
            embed.description += "- **Active proposals**\n"
            for i, proposal in enumerate(proposals, start=1):
                title = DAO.sanitize(proposal.message)
                tx_hash = "0x94d1bb675e2278e221daabc1e3f2564bc33fa2ce01f29e94e0165fbcd46e654f"  # TODO
                url = f"{cfg['rocketpool.execution_layer.explorer']}/tx/{tx_hash}"
                embed.description += f"  {i}. [{title}]({url})\n"
        else:
            embed.description += "- **No active proposals**\n"

        # --------- DAO FORUM --------- #

        embed.description += "### Forum\n"
        topics = await self._get_latest_forum_topics()
        if topics:
            embed.description += "- **Recently active topics**\n"
            for i, topic in enumerate(topics[:10], start=1):
                title = DAO.sanitize(topic.title)
                embed.description += f"  {i}. [{title}]({topic.url})\n"
        else:
            embed.description += "- **No recently active topics**\n"

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Governance(bot))
