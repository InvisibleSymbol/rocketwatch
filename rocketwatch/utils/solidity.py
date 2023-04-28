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

def date_to_beacon_block(date):
    return (date - BEACON_START_DATE) // 12

def slot_to_beacon_day_epoch_slot(slot):
    return slot // 32 // 225, slot // 32 % 225, slot % 32


SUBMISSION_KEYS = (
    "rewardIndex", "executionBlock", "consensusBlock", "merkleRoot", "merkleTreeCID", "intervalsPassed", "treasuryRPL",
    "trustedNodeRPL", "nodeRPL", "nodeETH", "userETH")


def mp_state_to_str(state):
    match state:
        case 0:
            return "initialised"
        case 1:
            return "prelaunch"
        case 2:
            return "staking"
        case 3:
            return "withdrawable"
        case 4:
            return "dissolved"
        case _:
            return str(state)
