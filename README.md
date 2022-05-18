# Rocket Watch

A Discord bot that tracks Rocket Pool Events

[![wakatime](https://wakatime.com/badge/github/InvisibleSymbol/rocketwatch.svg)](https://wakatime.com/badge/github/InvisibleSymbol/rocketwatch)
[![LGTM Grade](https://img.shields.io/lgtm/grade/python/github/InvisibleSymbol/rocketwatch?label=code%20quality&logo=lgtm)](https://lgtm.com/projects/g/InvisibleSymbol/rocketwatch/alerts/)

- Ability to track Proposals (Description/Vote Count read from Contract)
- Ability to track oDAO Member Activity (uses Nicknames of oDAO Members if available)
- Ability to track Deposit Poll Activity
- Ability to track Minipool Activity (Provides Link to Validator if feasible)
- Supports ENS Addresses
- Automatically retrieves Addresses from Storage Contract at start-up. (Easy support for Upgrades)
- Supports dual-channel setup to separate oDAO Events from the rest.
- Deduplication-Logic (prevents duplicated Messages caused by Chain-Reorgs).
- Easy Extendability (Almost no hard-coded Events, most are loaded from a `.json` File)
<!--
## Instructions:

- Python 3.8 Recommended
- `pip install -r requirements.txt`
- Copy `.env.sample` to `.env` and fill everything out. You can get the channel IDs by enabling Developer Mode in your
  Discord Settings and Right-Clicking a Channel.
- Run `python main.py`

## How to add new Events:

- Open `./data/rocketpool.json` and add a new Entry to `sources`. Map the Contract Events to new Bot Events.
- Add the required ABI in `./contracts/`. (The Path should look like this: `./contracts/rocketMinipoolManager.abi`).
- Open `./strings/rocketpool.en.json` and add both `title` and `description` for each new Bot Event. You can access
  Event Arguments directly using their Names: `%(amount)`. If you want to mention an Address, you can append `_fancy` to
  get a shorter Version that also automatically links to etherscan.io.

-->
## Donate:
[<kbd>0xinvis.eth</kbd>](https://etherscan.io/address/0xf0138d2e4037957d7b37de312a16a88a7f83a32a)
