import pickle
from datetime import datetime

from utils.cfg import cfg


def calc_score(block_number, transaction_index=999, event_index=999):
    return block_number + (transaction_index * 10 ** -3) + (event_index * 10 ** -6)


class Response:
    def __init__(self,
                 embed,
                 event_name,
                 unique_id,
                 block_number=2 ** 32,
                 transaction_index=999,
                 event_index=999
                 ):
        self.embed = embed
        self.event_name = event_name
        self.unique_id = unique_id
        self.block_number = block_number
        self.transaction_index = transaction_index
        self.event_index = event_index
        self.time_seen = datetime.utcnow()
        self.score = self.block_number + (self.transaction_index * 10 ** -3) + (self.event_index * 10 ** -6)
        # select channel dynamically from config based on event_name prefix
        channels = cfg["discord.channels"]
        channel_candidates = [value for key, value in channels.items() if event_name.startswith(key)]
        self.channel_id = channel_candidates[0] if channel_candidates else channels['default']

    def __bool__(self):
        return bool(self.embed)

    @staticmethod
    def get_embed(payload):
        return pickle.loads(payload["embed"])

    def to_dict(self):
        return {
            "_id"         : self.unique_id,
            "embed"       : pickle.dumps(self.embed),
            "event_name"  : self.event_name,
            "block_number": self.block_number,
            "score"       : self.score,
            "time_seen"   : self.time_seen,
            "channel_id"  : self.channel_id,
            "processed"   : False
        }
