import logging
from typing import Optional, Any

from eth_typing import BlockNumber 
from web3.contract import ContractEvent, LogReceipt

from utils.cfg import cfg

log = logging.getLogger("event_logs")
log.setLevel(cfg["log_level"])


def get_logs(
    event: ContractEvent, 
    from_block: BlockNumber, 
    to_block: BlockNumber, 
    arg_filters: Optional[dict[str, Any]] = None
) -> list[LogReceipt]:
    start_block = from_block
    end_block = to_block
    
    log.debug(f"Fetching vote receipts in [{start_block}, {end_block}]")

    chunk_size = 50_000
    from_block = start_block
    to_block = from_block + chunk_size
    
    logs = []
    
    while from_block <= end_block:
        logs += event.create_filter(
            fromBlock=from_block, 
            toBlock=min(to_block, end_block), 
            argument_filters=arg_filters
        ).get_all_entries()
        
        from_block = to_block + 1
        to_block = from_block + chunk_size
    
    return logs
