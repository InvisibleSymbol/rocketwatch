import datetime
import logging
from io import BytesIO

import aiohttp
import matplotlib.pyplot as plt
import pytz
import requests
from discord import File
from discord.ext import commands
from discord.ext.commands import Context, hybrid_command
from icalendar import Calendar

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("sleep")
log.setLevel(cfg["log_level"])


class Oura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.calendar_url = cfg["oura.calendar_url"]

    async def get_calendar_data(self):
        d = {}
        async with aiohttp.ClientSession() as session:
            async with session.get(self.calendar_url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.text()
        cal = Calendar.from_ical(data)
        for event in cal.walk("VEVENT"):
            sd = event["DTSTART"].dt.replace(tzinfo=pytz.UTC).astimezone(
                    pytz.timezone("Europe/Vienna"))
            sd_r = sd + datetime.timedelta(days=1) if sd > sd.replace(hour=18, minute=0, second=0) else sd
            start_day = sd_r.strftime("%Y-%m-%d")
            ed = event["DTEND"].dt.replace(tzinfo=pytz.UTC).astimezone(
                    pytz.timezone("Europe/Vienna"))
            ed_r = ed + datetime.timedelta(days=1) if ed > ed.replace(hour=18, minute=0, second=0) else ed
            end_day = ed_r.strftime("%Y-%m-%d")
            if start_day not in d:
                d[start_day] = []
            if end_day not in d:
                d[end_day] = []
            thresh = datetime.datetime(year=ed.year, month=ed.month, day=ed.day, hour=18,
                                       tzinfo=ed.tzinfo)
            if start_day != end_day:
                dur_first = thresh - sd
                if dur_first >= datetime.timedelta(hours=24):
                    dur_first -= datetime.timedelta(hours=24)
                dur_second = ed - thresh
                if dur_second <= datetime.timedelta(hours=0):
                    dur_second += datetime.timedelta(hours=24)
                total_dur = dur_first + dur_second
                # split stats into two parts based on duration of each part
                d[start_day].append(
                    {"relative_start": sd - (thresh - datetime.timedelta(days=1)), "duration": dur_first})
                d[end_day].append(
                    {"relative_start": datetime.timedelta(), "duration": dur_second})
            else:
                relative_start = sd - (thresh - datetime.timedelta(days=1))
                if relative_start >= datetime.timedelta(hours=24):
                    relative_start -= datetime.timedelta(hours=24)
                d[start_day].append(
                    {"relative_start": relative_start, "duration": ed - sd})
        return d


    @hybrid_command()
    async def sleep_schedule(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(title="Invis's Sleep Schedule")
        current_date = datetime.datetime.now()
        tz = pytz.timezone("Europe/Vienna")
        start_date = current_date - datetime.timedelta(days=120)
        # make start date timezone aware
        start_date = tz.localize(start_date)
        end_date = current_date
        # make end date timezone aware
        end_date = tz.localize(end_date)
        res = requests.get("https://api.ouraring.com/v2/usercollection/sleep",
                           params={"start_date": start_date.strftime("%Y-%m-%d"),
                                   "end_date"  : (end_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")},
                           headers={"Authorization": f"Bearer {cfg['oura.secret']}"})
        if res.status_code != 200:
            e.description = "Error fetching sleep data"
            await ctx.send(embed=e)
            return
        data = res.json()
        if len(data["data"]) == 0:
            e.description = "No sleep data found"
            await ctx.send(embed=e)
            return

        daily_sleep = {
            (start_date + datetime.timedelta(days=i)).strftime("%Y-%m-%d"): []
            for i in range((end_date - start_date).days + 1)}

        for sleep in data["data"]:
            if sleep["type"] == "rest":
                continue
            # skip if sleep_duration is less than 30 minutes. units are in seconds
            if sleep["total_sleep_duration"] < 30 * 60:
                continue
            sd = datetime.datetime.fromisoformat(sleep["bedtime_start"])
            log.info(f"start date: {sd}")
            # the start day is the next day if we are past 12pm, otherwise it is the current day
            start_day_r = sd + datetime.timedelta(days=1) if sd.hour >= 18 else sd
            # format to string
            start_day = start_day_r.strftime("%Y-%m-%d")
            ed = datetime.datetime.fromisoformat(sleep["bedtime_end"])
            log.info(f"end date: {ed}")
            # the end day is the next day if we are past 12pm, otherwise it is the current day
            ed_r = ed + datetime.timedelta(days=1) if ed.hour >= 18 else ed
            # format to string
            end_day = ed_r.strftime("%Y-%m-%d")
            thresh = datetime.datetime(year=ed.year, month=ed.month, day=ed.day, hour=18,
                                       tzinfo=ed.tzinfo)
            if start_day not in daily_sleep:
                daily_sleep[start_day] = []
            if end_day not in daily_sleep:
                daily_sleep[end_day] = []
            # weekday based on start date
            weekday = datetime.datetime.fromisoformat(start_day).weekday()
            stats = sleep["sleep_phase_5_min"]
            if start_day != end_day:
                dur_first = thresh - sd
                if dur_first >= datetime.timedelta(hours=24):
                    dur_first -= datetime.timedelta(hours=24)
                dur_second = ed - thresh
                if dur_second <= datetime.timedelta(hours=0):
                    dur_second += datetime.timedelta(hours=24)
                total_dur = dur_first + dur_second
                # split stats into two parts based on duration of each part
                stats_first = stats[:int(len(stats) * (dur_first.total_seconds() / total_dur.total_seconds()))]
                stats_second = stats[int(len(stats) * (dur_first.total_seconds() / total_dur.total_seconds())):]
                daily_sleep[start_day].append(
                    {"relative_start": sd - (thresh - datetime.timedelta(days=1)), "duration": dur_first,
                     "weekday"       : weekday, "sleep_stats": stats_first})
                if end_day not in daily_sleep:
                    daily_sleep[end_day] = []
                daily_sleep[end_day].append(
                    {"relative_start": datetime.timedelta(), "duration": dur_second, "weekday": weekday,
                     "sleep_stats"   : stats_second})
            else:
                relative_start = sd - (thresh - datetime.timedelta(days=1))
                if relative_start >= datetime.timedelta(hours=24):
                    relative_start -= datetime.timedelta(hours=24)
                daily_sleep[start_day].append(
                    {"relative_start": relative_start, "duration": ed - sd, "weekday": weekday,
                     "sleep_stats"   : stats})
        # sort by date
        daily_sleep = dict(sorted(daily_sleep.items(), key=lambda x: x[0]))
        day_of_week_colors = ["#ff0000", "#ff8000", "#ffff00", "#80ff00", "#00ff00", "#00ff80", "#00ffff"]
        # plot
        fig, ax = plt.subplots()
        # create horizontal dark gray line at midnight and noon
        ax.axhline(y=18, color="#808080", linewidth=1)
        ax.axhline(y=18 - 12, color="#808080", linewidth=1)
        calendar_data = await self.get_calendar_data()
        # render calendar data if they are within the last 180 days
        if calendar_data is not None:
            for day, data in calendar_data.items():
                for d in data:
                    bottom = ((24 * 60 * 60) - d[
                        "relative_start"].total_seconds() - d["duration"].total_seconds()) / 3600
                    width = d["duration"].total_seconds() / 3600
                    try:
                        i = list(daily_sleep.keys()).index(day)
                    except ValueError:
                        continue
                    ax.bar(i, width, bottom=bottom, color="#AAAAAA", width=1, alpha=0.5)
        for i, (day, sleeps) in enumerate(daily_sleep.items()):
            for sleep in sleeps:
                color = day_of_week_colors[sleep["weekday"]]
                bottom = ((24 * 60 * 60) - sleep[
                    "relative_start"].total_seconds() - sleep["duration"].total_seconds()) / 3600
                width = sleep["duration"].total_seconds() / 3600
                current_bottom = bottom + width
                for state in sleep["sleep_stats"]:
                    current_bottom -= (width / len(sleep["sleep_stats"]))
                    ax.bar(i, width / len(sleep["sleep_stats"]), bottom=current_bottom, color=color,
                           alpha=0 if state == "4" else 0.8)
        # set x axis labels, only every 7th day
        ax.set_xticks(range(len(daily_sleep) - 1, 0, -14))
        ax.set_xticklabels([day for i, (day, _) in enumerate(reversed(daily_sleep.items())) if i % 14 == 0])
        # set y axis labels
        ax.set_yticks(range(0, 25, 2))
        ax.set_yticklabels([f"{i}:00" if i >= 0 else f"{24 + i}:00" for i in range(18, -7, -2)])
        # set y limit
        ax.set_ylim(0, 24)
        # set x limit
        ax.set_xlim(-1, len(daily_sleep))
        # grid
        ax.grid(True)
        ax.set_axisbelow(True)
        # set title
        ax.set_title("Invis's Sleep Schedule")

        # reduce padding
        plt.tight_layout()

        img = BytesIO()
        fig.savefig(img, format='png')
        img.seek(0)
        plt.close()

        e.set_image(url="attachment://sleep.png")
        buf = File(img, filename="sleep.png")
        # send image
        await ctx.send(file=buf, embed=e)


async def setup(bot):
    await bot.add_cog(Oura(bot))
