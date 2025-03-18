import json
import logging

import pymongo
from web3.datastructures import MutableAttributeDict as aDict

from rocketwatch import RocketWatch
from utils import solidity
from utils.cfg import cfg
from utils.embeds import assemble
from utils.rocketpool import rp
from utils.event import EventPlugin, Event

log = logging.getLogger("milestones")
log.setLevel(cfg["log_level"])


class Milestones(EventPlugin):
    def __init__(self, bot: RocketWatch):
        super().__init__(bot)
        self.db = pymongo.MongoClient(cfg["mongodb.uri"]).rocketwatch
        self.collection = self.db.milestones
        self.state = "OK"

        with open("./plugins/milestones/milestones.json") as f:
            self.milestones = json.load(f)

    def _get_new_events(self) -> list[Event]:
        if self.state == "RUNNING":
            log.error("Milestones plugin was interrupted while running. Re-initializing...")
            self.__init__(self.bot)

        self.state = "RUNNING"
        result = self.check_for_new_events()
        self.state = "OK"
        return result

    # noinspection PyTypeChecker
    def check_for_new_events(self):
        log.info("Checking Milestones")
        payload = []

        for milestone in self.milestones:
            milestone = aDict(milestone)

            state = self.collection.find_one({"_id": milestone["id"]})

            value = getattr(rp, milestone.function)(*milestone.args)
            if milestone.formatter:
                value = getattr(solidity, milestone.formatter)(value)
            log.debug(f"{milestone.id}:{value}")
            if value < milestone.min:
                continue

            step_size = milestone.step_size
            latest_goal = (value // step_size + 1) * step_size

            if state:
                previous_milestone = state["current_goal"]
            else:
                log.debug(
                    f"First time we have processed Milestones for milestone {milestone.id}. Adding it to the Database.")
                self.collection.insert_one({"_id": milestone["id"], "current_goal": latest_goal})
                previous_milestone = milestone.min
            if previous_milestone < latest_goal:
                log.info(f"Goal for milestone {milestone.id} has increased. Triggering Milestone!")
                embed = assemble(aDict({
                    "event_name"  : milestone.id,
                    "result_value": value
                }))
                payload.append(Event(
                    embed=embed,
                    topic="milestones",
                    block_number=self._pending_block,
                    event_name=milestone.id,
                    unique_id=f"{milestone.id}:{latest_goal}",
                ))
                # update the current goal in collection
                self.collection.update_one({"_id": milestone["id"]}, {"$set": {"current_goal": latest_goal}})

        log.debug("Finished Checking Milestones")
        return payload


async def setup(bot):
    await bot.add_cog(Milestones(bot))
