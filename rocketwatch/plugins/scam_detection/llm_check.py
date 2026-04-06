import json
import logging
from typing import Any

from discord import Member, Message

from rocketwatch.utils.config import cfg
from rocketwatch.utils.llm import LLMProvider, create_provider

log = logging.getLogger("rocketwatch.scam_detection.llm")

MAX_REASON_WORDS = 5
MAX_OUTPUT_TOKENS = (MAX_REASON_WORDS + 1) * 3

SYSTEM_PROMPT = f"""\
You are a scam detection system for a cryptocurrency Discord server.
Your job is to determine whether a message is attempting to manipulate or deceive users.

Focus on the author's INTENT. Flag messages that are trying to:
- Lure users away from public channels (into DMs, external platforms, profiles, etc.)
- Build false trust by impersonating authority (staff, support, admins)
- Create artificial urgency or fear to pressure users into action
- Deceive users through any other social engineering technique

You will be given context about the user: their join date and number of previous \
messages in the server. Brand-new users with few or no messages who jump straight \
into offering help or directing others are highly suspicious.

Do NOT flag messages that:
- Mention DMs, profiles, or wallets in normal conversation without manipulative intent
- Offer genuine (even if clumsy) technical help
- Discuss problems, errors, or frustrations — even emotional ones
- Contain links shared in good faith

Examples:

"I've sent you a guide, kindly check. I had a similar issue but it was resolved"
-> SCAM: Steering to DMs

"Apologies for the inconvenience. For any inquiries or support, please use the official link in my bio to reach the technical team and moderators."
-> SCAM: Profile link redirection

"You need assistance mate?"
-> SCAM: Unsolicited help offer

"This support is useless, where do I actually get help?"
-> SAFE

"Can someone explain how the minipool bond reduction works?"
-> SAFE

"My node has been offline for 2 days and I keep getting penalties, is there something wrong with the network?"
-> SAFE

Respond with SAFE or SCAM: <reason in {MAX_REASON_WORDS} words max>"""

USER_PROMPT_TEMPLATE = (
    "User joined: {joined}\n"
    "Previous messages in server: {message_count}\n\n"
    "Evaluate this Discord message:\n\n{content}"
)


class LLMScamChecker:
    def __init__(self) -> None:
        self._provider: LLMProvider | None = create_provider(cfg.llm)
        self.enabled = self._provider is not None

    async def check(self, message: Message, *, user_msg_count: int) -> str | None:
        """Evaluate a message for social engineering using an LLM.

        Returns a reason string if the message is likely a scam, None otherwise.
        """
        if not self._provider:
            return None

        data: dict[str, Any] = {"content": message.content}
        if message.embeds:
            data["embeds"] = [
                {"title": e.title, "description": e.description} for e in message.embeds
            ]
        content = json.dumps(data, indent=2)

        joined = "unknown"
        if isinstance(message.author, Member) and message.author.joined_at:
            joined = message.author.joined_at.strftime("%Y-%m-%d")

        prev_user_msg_count = user_msg_count - 1

        user_message = USER_PROMPT_TEMPLATE.format(
            content=content, joined=joined, message_count=prev_user_msg_count
        )
        result = (
            await self._provider.complete(
                SYSTEM_PROMPT, user_message, max_tokens=MAX_OUTPUT_TOKENS
            )
        ).strip()
        log.debug(f"AI scam check ({cfg.llm.provider}/{cfg.llm.model}): {result}")

        if result.upper().startswith("SCAM"):
            reason = result.removeprefix("SCAM").lstrip(":").strip()
            reason = reason.split("\n")[0].rstrip(".")
            if reason:
                return reason

        return None
