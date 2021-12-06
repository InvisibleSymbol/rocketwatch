import asyncio
import json
import logging

from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict
import motor.motor_asyncio
import pymongo

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble
from utils.rocketpool import rp

log = logging.getLogger("milestones")
log.setLevel(cfg["log_level"])


class QueuedMilestones(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = "OK"
        self.state = {}
        self.mongo = pymongo.MongoClient('mongodb://localhost:27017')
        self.db = self.mongo.rocketwatch
        self.collection = self.db.milestones

        with open("./plugins/milestones/milestones.json") as f:
            self.milestones = json.load(f)

    def run_loop(self):
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
                log.debug(f"First time we have processed Milestones for milestone {milestone.id}. Adding it to the Database.")
                self.collection.insert_one({"_id": milestone["id"], "current_goal": latest_goal})
                previous_milestone = milestone.min
            if previous_milestone < latest_goal:
                log.info(f"Goal for milestone {milestone.id} has increased. Triggering Milestone!")
                embed = assemble(aDict({
                    "event_name"     : milestone.id,
                    "result_value"   : value
                }))
                payload.append(Response(
                    embed=embed,
                    event_name=milestone.id,
                    unique_id=f"{milestone.id}:{latest_goal}",
                ))
                # update the current goal in collection
                self.collection.update_one({"_id": milestone["id"]}, {"$set": {"current_goal": latest_goal}})

        log.debug("Finished Checking Milestones")
        return payload


def setup(bot):
    bot.add_cog(QueuedMilestones(bot))
