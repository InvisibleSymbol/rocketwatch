import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from discord import ButtonStyle, Color, Interaction, Member, ui

from rocketwatch.utils.config import cfg

if TYPE_CHECKING:
    from rocketwatch.bot import RocketWatch

log = logging.getLogger("rocketwatch.scam_detection")


def is_reputable(user: Member) -> bool:
    return any(
        (
            user.id == cfg.discord.owner.user_id,
            user.id in cfg.rocketpool.support.user_ids,
            {role.id for role in user.roles} & set(cfg.rocketpool.support.role_ids),
            user.guild_permissions.moderate_members,
        )
    )


class ReportColor:
    ALERT = Color.from_rgb(255, 0, 0)
    WARN = Color.from_rgb(255, 165, 0)
    OK = Color.from_rgb(0, 255, 0)


class ScamReport(TypedDict):
    type: str
    guild_id: int | None
    user_id: int
    reason: str
    content: str | None
    warning_id: int | None
    report_id: int
    user_banned: bool
    channel_id: NotRequired[int]
    message_id: NotRequired[int]
    embeds: NotRequired[list[dict[str, Any]]]
    removed: NotRequired[bool]


class WarningConfirmView(ui.View):
    def __init__(
        self,
        on_confirm: Callable[[], Awaitable[None]],
        on_dismiss: Callable[[Member], Awaitable[None]],
    ):
        super().__init__(timeout=None)
        self._on_confirm = on_confirm
        self._on_dismiss = on_dismiss

    async def _check_reputable(self, interaction: Interaction["RocketWatch"]) -> bool:
        if isinstance(interaction.user, Member) and is_reputable(interaction.user):
            return True
        await interaction.response.send_message(
            content="Only moderators can confirm or dismiss reports.", ephemeral=True
        )
        return False

    @ui.button(label="Confirm", style=ButtonStyle.danger)
    async def confirm(
        self,
        interaction: Interaction["RocketWatch"],
        button: ui.Button["WarningConfirmView"],
    ) -> None:
        if not await self._check_reputable(interaction):
            return
        await interaction.response.edit_message(view=None)
        await self._on_confirm()

    @ui.button(label="Dismiss", style=ButtonStyle.success)
    async def dismiss(
        self,
        interaction: Interaction["RocketWatch"],
        button: ui.Button["WarningConfirmView"],
    ) -> None:
        if not await self._check_reputable(interaction):
            return
        assert isinstance(interaction.user, Member)
        await interaction.message.delete()  # type: ignore[union-attr]
        await self._on_dismiss(interaction.user)


class ReportReviewView(ui.View):
    def __init__(
        self,
        on_confirm: Callable[[Member], Awaitable[None]],
        on_dismiss: Callable[[Member], Awaitable[None]],
    ):
        super().__init__(timeout=None)
        self._on_confirm = on_confirm
        self._on_dismiss = on_dismiss

    async def _check_moderator(self, interaction: Interaction["RocketWatch"]) -> bool:
        if (
            isinstance(interaction.user, Member)
            and interaction.user.guild_permissions.ban_members
        ):
            return True
        await interaction.response.send_message(
            content="Only moderators can review reports.", ephemeral=True
        )
        return False

    @ui.button(label="Confirm Scam", style=ButtonStyle.danger)
    async def confirm(
        self,
        interaction: Interaction["RocketWatch"],
        button: ui.Button["ReportReviewView"],
    ) -> None:
        if not await self._check_moderator(interaction):
            return
        assert isinstance(interaction.user, Member)
        await interaction.response.edit_message(view=None)
        await self._on_confirm(interaction.user)

    @ui.button(label="Mark Safe", style=ButtonStyle.success)
    async def dismiss(
        self,
        interaction: Interaction["RocketWatch"],
        button: ui.Button["ReportReviewView"],
    ) -> None:
        if not await self._check_moderator(interaction):
            return
        assert isinstance(interaction.user, Member)
        await interaction.response.edit_message(view=None)
        await self._on_dismiss(interaction.user)
