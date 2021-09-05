# rpl-bot
 A Discord bot that tracks Rocket Pool Events

# Instructions
- Python 3.8 Recommended
- `pip install -r requirements.txt`
- Copy `.env.sample` to `.env` and fill everything out. You can get the channel IDs by enabling Developer Mode in your Discord Settings and Right-Clicking a Channel.
- Run `python main.py`


# How to add new Events:
- Open `rocketpool.json` and add a new Entry. Map the Contract Events to Bot Events in `events_to_watch`.
- Add the required ABI in `./contracts/`. (The Path should look like this: `./contracts/RocketMinipoolManager.abi`). 
- Open `./strings/rocketpool.en.json` and add both `title` and `description` for each new Event. You can access Event Arguments directly using their Names: `%(amount)`. If you want to mention an Address, you can append `_fancy` to get a shorter Version that also automatically links to etherscan.io.

# Donate: 
[<kbd>0x87FF5B8ccFAeEC77b2B4090FD27b11dA2ED808Fb</kbd>](https://invis.cloud/donate)