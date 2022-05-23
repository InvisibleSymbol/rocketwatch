async def get_or_fetch_channel(bot, channel_id):
    channel = bot.get_channel(channel_id)
    if not channel:
        channel = await bot.fetch_channel(channel_id)
    return channel
