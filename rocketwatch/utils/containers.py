import pickle

from io import BytesIO
from typing import Optional
from datetime import datetime

from discord import File
from PIL.Image import Image

from utils.cfg import cfg
from utils.embeds import Embed


class Event:
    def __init__(
            self,
            embed : Embed,
            topic: str,
            event_name: str,
            unique_id: str,
            block_number: int,
            transaction_index: int = 999,
            event_index: int = 999,
            attachment: Optional[Image] = None
    ):
        self.embed = embed
        self.topic = topic
        self.event_name = event_name
        self.unique_id = unique_id
        self.block_number = block_number
        self.transaction_index = transaction_index
        self.event_index = event_index
        self.attachment = attachment

        self.time_seen = datetime.now()
        self.score = self.block_number * 10 ** 9 + self.transaction_index * 10 ** 5 + self.event_index
        if self.embed.footer and self.embed.footer.text:
            self.embed.set_footer_parts([f"score: {self.score}"])

        # select channel dynamically from config based on event_name prefix
        channels = cfg["discord.channels"]
        channel_candidates = [value for key, value in channels.items() if event_name.startswith(key)]
        self.channel_id = channel_candidates[0] if channel_candidates else channels['default']

    def __bool__(self):
        return bool(self.embed)

    @staticmethod
    def load_embed(event_dict: dict) -> Embed:
        return pickle.loads(event_dict["embed"])

    @staticmethod
    def load_attachment(event_dict: dict) -> Optional[File]:
        serialized = event_dict.get("attachment")
        if not serialized:
            return None

        try:
            img = pickle.loads(serialized)
        except Exception:
            return None

        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return File(buffer, f"{event_dict['event_name']}.png")

    def to_dict(self) -> dict:
        return {
            "_id"         : self.unique_id,
            "embed"       : pickle.dumps(self.embed),
            "topic"       : self.topic,
            "event_name"  : self.event_name,
            "block_number": self.block_number,
            "score"       : self.score,
            "time_seen"   : self.time_seen,
            "attachment"  : pickle.dumps(self.attachment),
            "channel_id"  : self.channel_id,
            "processed"   : False
        }
