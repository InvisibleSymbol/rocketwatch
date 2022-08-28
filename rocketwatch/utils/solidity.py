# solidity units
seconds = 1
minutes = 60 * seconds
hours = 60 * minutes
days = 24 * hours
weeks = 7 * days
years = 365 * days

# beaconchain stuff that I can't dynamically get yet
BEACON_START_DATE = 1606824023
BEACON_EPOCH_LENGTH = 12 * 32


def to_float(n, decimals=18):
    return int(n) / 10 ** decimals


def to_int(n, decimals=18):
    return int(n) // 10 ** decimals


def beacon_block_to_date(block_num):
    return BEACON_START_DATE + (block_num * 12)


SUBMISSION_KEYS = (
    "rewardIndex", "executionBlock", "consensusBlock", "merkleRoot", "merkleTreeCID", "intervalsPassed", "treasuryRPL",
    "trustedNodeRPL", "nodeRPL", "nodeETH", "userETH")
