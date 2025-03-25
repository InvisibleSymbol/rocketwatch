import logging
from datetime import datetime, timedelta

from discord.ext.commands import Context, hybrid_command
from discord.utils import escape_markdown
from eth_typing import HexStr
from web3.constants import HASH_ZERO

from plugins.snapshot.snapshot import Snapshot
from plugins.forum.forum import Forum
from plugins.rpips.rpips import RPIPs

from utils.status import StatusPlugin
from utils.cfg import cfg
from utils.dao import DAO, DefaultDAO, OracleDAO, SecurityCouncil, ProtocolDAO
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
        return [dao.fetch_proposal(proposal_id) for proposal_id in reversed(active_proposal_ids)]

    @staticmethod
    def _get_active_dao_proposals(dao: DefaultDAO) -> list[DefaultDAO.Proposal]:
        proposals = dao.get_proposals_by_state()
        active_proposal_ids = proposals[dao.ProposalState.Active]
        return [dao.fetch_proposal(proposal_id) for proposal_id in reversed(active_proposal_ids)]

    @staticmethod
    def _get_tx_hash_for_proposal(dao: DAO, proposal: DAO.Proposal) -> HexStr:
        from_block = get_block_by_timestamp(proposal.created)[0] - 1
        to_block = get_block_by_timestamp(proposal.created)[0] + 1

        log.info(f"Looking for proposal {proposal} in [{from_block},{to_block}]")
        for receipt in dao.proposal_contract.events.ProposalAdded().get_logs(fromBlock=from_block, toBlock=to_block):
            log.info(f"Found receipt {receipt}")
            if receipt.args.proposalID == proposal.id:
                return receipt.transactionHash.hex()

        return HexStr(HASH_ZERO)

    async def _get_active_snapshot_proposals(self) -> list[Snapshot.Proposal]:
        try:
            return Snapshot.fetch_proposals("active", reverse=True)
        except Exception as e:
            await self.bot.report_error(e)
            return []

    async def _get_draft_rpips(self) -> list[RPIPs.RPIP]:
        try:
            return [rpip for rpip in RPIPs.get_all_rpips() if (rpip.status == "Draft")][::-1]
        except Exception as e:
            await self.bot.report_error(e)
            return []

    async def _get_latest_forum_topics(self, days: int) -> list[Forum.Topic]:
        try:
            topics = await Forum.get_recent_topics()
            now = datetime.now().timestamp()
            # only get topics from within a week
            topics = [t for t in topics if (now - t.last_post_at) <= timedelta(days=days).total_seconds()]
            return topics
        except Exception as e:
            await self.bot.report_error(e)
            return []

    async def get_digest(self) -> Embed:
        embed = Embed(title="Governance Digest", description="")

        def sanitize(text: str, max_length=50) -> str:
            text = text.strip()
            text = text.replace("http://", "")
            text = text.replace("https://", "")
            text = escape_markdown(text)
            if len(text) > max_length:
                text = text[:(max_length - 1)] + "…"
            return text

        def print_proposals(_dao: DAO, _proposals: list[DAO.Proposal]) -> str:
            text = ""
            for _i, _proposal in enumerate(_proposals, start=1):
                _title = sanitize(_proposal.message)
                _tx_hash = self._get_tx_hash_for_proposal(_dao, _proposal)
                _url = f"{cfg['execution_layer.explorer']}/tx/{_tx_hash}"
                text += f"  {_i}. [{_title}]({_url})\n"
            return text

        # --------- PROTOCOL DAO --------- #

        section_content = ""
        dao = ProtocolDAO()

        if proposals := self._get_active_pdao_proposals(dao):
            section_content += "- **Active on-chain proposals**\n"
            section_content += print_proposals(dao, proposals)

        if snapshot_proposals := await self._get_active_snapshot_proposals():
            section_content += "- **Active Snapshot proposals**\n"
            for i, proposal in enumerate(snapshot_proposals, start=1):
                title = sanitize(proposal.title)
                section_content += f"  {i}. [{title}]({proposal.url})\n"

        if draft_rpips := await self._get_draft_rpips():
            section_content += "- **RPIPs in draft status**\n"
            for i, rpip in enumerate(draft_rpips, start=1):
                title = sanitize(rpip.title, 40)
                section_content += f"  {i}. [{title}]({rpip.url}) (RPIP-{rpip.number})\n"

        if section_content:
            embed.description += "### Protocol DAO\n"
            embed.description += section_content

        # --------- ORACLE DAO --------- #

        dao = OracleDAO()
        if proposals := self._get_active_dao_proposals(dao):
            embed.description += "### Oracle DAO\n"
            embed.description += "- **Active proposals**\n"
            embed.description += print_proposals(dao, proposals)

        # --------- SECURITY COUNCIL --------- #

        dao = SecurityCouncil()
        if proposals := self._get_active_dao_proposals(dao):
            embed.description += "### Security Council\n"
            embed.description += "- **Active proposals**\n"
            embed.description += print_proposals(dao, proposals)

        # --------- DAO FORUM --------- #

        num_days = 7
        if topics := await self._get_latest_forum_topics(days=num_days):
            embed.description += "### Forum\n"
            embed.description += f"- **Recently active topics ({num_days}d)**\n"
            for i, topic in enumerate(topics[:10], start=1):
                title = sanitize(topic.title, 40)
                embed.description += f"  {i}. [{title}]({topic.url}) [`{topic.post_count-1}` 💬]\n"

        if not embed.description:
            embed.set_image(url="https://c.tenor.com/PVf-csSHmu8AAAAd/tenor.gif")

        return embed

    @hybrid_command()
    async def governance_digest(self, ctx: Context) -> None:
        """Get a summary of recent activity in protocol governance"""
        await ctx.defer(ephemeral=is_hidden_weak(ctx))
        embed = await self.get_digest()
        await ctx.send(embed=embed)

    async def get_status(self) -> Embed:
        embed = await self.get_digest()
        embed.title = ":classical_building: Live Governance Digest"
        return embed


async def setup(bot):
    await bot.add_cog(Governance(bot))
