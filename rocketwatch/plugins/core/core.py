import json
import logging

import termplotlib as tpl
from cachetools import FIFOCache
from discord.ext import commands, tasks
from web3.datastructures import MutableAttributeDict as aDict
from web3.exceptions import ABIEventFunctionNotFound

from utils import solidity
from utils.cfg import cfg
from utils.containers import Response
from utils.embeds import assemble, prepare_args, exception_fallback
from utils.reporter import report_error
from utils.rocketpool import rp
from utils.shared_w3 import w3

log = logging.getLogger("core")
log.setLevel(cfg["log_level"])


class Core(commands.Cog):
    message_queue = []
    
    def __init__(self, bot):
        self.bot = bot
        self.state = "OK"
        self.channels = cfg["discord.channels"]

        if not self.run_loop.is_running():
            self.run_loop.start()

    @tasks.loop(seconds=15.0)
    async def run_loop(self):
        if self.state == "STOPPED":
            return

        if self.state != "ERROR":
            try:
                self.state = "OK"
                return await self.gather_and_process_messages()
            except Exception as err:
                self.state = "ERROR"
                await report_error(err)
        try:
            return self.__init__(self.bot)
        except Exception as err:
            await report_error(err)

    async def gath_and_process_messages(self):
        log.info("Gathering messages from submodules")
        # so should these be in a folder inside the core pluging folder?
        # because they dont really have to be discordpy plugins
        # i just need a function i can call with a start block number that returns messages

        # while im writing this, i need a better more global scoring function.
        # milestones should be the last thing sent within a block, rest has to respect order
        # maybe `score = block_number + (transaction_index * 10^-3) + (event_index * 10^-6)`?
        # what is unspecified simply falls back to 999
        # so like `def calc_score(block_number, transaction_index=999, event_index=999)`

        # okay so it would be really cool if the bot sent a message in the default channel
        # if it fails run any submodule
        # need to keep track of the sent warning message and delete it if it works again.
        # we already need some kind of db so it doesnt sound too hard to add

        # temporary(?) submodule logic
        tmp_message_queue =  []
        for submodule in ["events", "transactions", "milestone"]:
            log.debug(f"Running submodule {submodule}...")
            response = []
            try:
                # async thread call here???????????????????????
                # async would be very epic but would require the submodules to be stateless
                # how do i prevent missing messages though if a reorg moves new transactions into a block i have already checked?
                # okay so use a mongodb collection to keep track of (block_id, tnx_hash) for dedupping
                # automatically discard entries that have a blocknumber older than reorg_distances * 2
                # when the submodule starts it starts the filter from reorg_distance blocks away and gives me all new message
                # then discard any transactions that we have seen no matter what block they were in- we only want to prevent dups

                # await Thread(submodule.entry, args=[start_block, end_block]).join()
                raise NotImplemented()
            except Exception as err:
                log.warning(f"Failed to run submodule {submodule}. Discarding temporary messages")
                report_error(err)
                return

            if response:<
                log.debug(f"Got {len(response)} new messages from submodule")
                self.message_queue.extend(response)
                # TODO keep track of the last message block so we can start from there on the next loop
                # store this in a db though. heck maybe also store the queue while we are doing that

        if self.message_queue:
            log.info("Processing {len(self.message_queue)} Messages in queue")
            # sort messages by score
            self.message_queue.sort(key=lambda a: a["score"])
            # try to send messages in order and stop if we fail to send one
            # we can simply try again on the next run anyways, the list is permanent
            while self.message_queue:
                # get youngest message first
                msg = self.message_queue[0]
                # select channel for message
                channel_candidates = [value for key, value in self.channels.items() if msg.result.event_name.startswith(key)]
                try:
                    channel = await self.bot.fetch_channel(channel_candidates[0] if channel_candidates else self.channels['default'])
                except Exception as err:
                    report_error(err)
                await channel.send(embed=message.result.embed)

            log.info("Finished sending Message(s)")

    def cog_unload(self):
        self.state = "STOPPED"
        self.run_loop.cancel()


def setup(bot):
    bot.add_cog(Events(bot))
