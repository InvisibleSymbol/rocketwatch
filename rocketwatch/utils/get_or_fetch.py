async def get_or_fetch_channel(bot, channel_id):
    return bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
