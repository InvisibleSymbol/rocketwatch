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
import matplotlib.colors as mcolors
from homeassistant_api import Client, Entity
from matplotlib import dates

from utils.cfg import cfg
from utils.embeds import Embed
from utils.visibility import is_hidden

log = logging.getLogger("sleep")
log.setLevel(cfg["log_level"])


def get_color_hsv(value):
    # Ensure the value is within [0, 1]
    value -= 0.5
    value *= 2
    value = max(0, min(1, value))

    # Map the value to the hue in HSV (red to green)
    hue = value / 3  # Red is at 0, green is at 1/3 in HSV space
    color_hsv = (hue, 1, 0.8)  # Full saturation and value

    # Convert HSV to RGB
    return mcolors.hsv_to_rgb(color_hsv)

class Oura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.calendar_url = cfg["oura.calendar_url"]

    @hybrid_command()
    async def sleep_schedule(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))
        e = Embed(title="Invis's Sleep Schedule")
        current_date = datetime.datetime.now()
        tz = pytz.timezone("Europe/Vienna")
        start_date = current_date - datetime.timedelta(days=150)
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

        res2 = requests.get("https://api.ouraring.com/v2/usercollection/daily_sleep",
                           params={"start_date": start_date.strftime("%Y-%m-%d"),
                                   "end_date"  : (end_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")},
                           headers={"Authorization": f"Bearer {cfg['oura.secret']}"})
        if res2.status_code != 200:
            e.description = "Error fetching sleep data"
            await ctx.send(embed=e)
            return
        data2 = res2.json()
        score_mapping = dict()
        if len(data2["data"]) != 0:
            for kasd in data2["data"]:
                score_mapping[kasd["day"]] = kasd["score"]
        daily_sleep = {
            (start_date + datetime.timedelta(days=i)).strftime("%Y-%m-%d"): []
            for i in range((end_date - start_date).days + 1)}

        for sleep in reversed(data["data"]):
            if sleep["type"] == "rest":
                continue
            # skip if sleep_duration is less than 30 minutes. units are in seconds
            if sleep["total_sleep_duration"] < 30 * 60:
                continue
            score = score_mapping.get(sleep["day"])
            hr = sleep["lowest_heart_rate"]
            hrv = sleep["average_hrv"]
            temperature = sleep["readiness"]["temperature_trend_deviation"]
            sd = datetime.datetime.fromisoformat(sleep["bedtime_start"]).astimezone(tz=tz)
            log.info(f"start date: {sd}")
            # the start day is the next day if we are past 12pm, otherwise it is the current day
            start_day_r = sd + datetime.timedelta(days=1) if sd.hour >= 18 else sd
            # format to string
            start_day = start_day_r.strftime("%Y-%m-%d")
            ed = datetime.datetime.fromisoformat(sleep["bedtime_end"]).astimezone(tz=tz)
            log.info(f"end date: {ed}")
            # the end day is the next day if we are past 12pm, otherwise it is the current day
            ed_r = ed + datetime.timedelta(days=1) if ed.hour >= 18 else ed
            # format to string
            end_day = ed_r.strftime("%Y-%m-%d")
            thresh = datetime.datetime(year=ed.year, month=ed.month, day=ed.day, hour=18,
                                       tzinfo=ed.tzinfo)
            # Define virtual day start at 18:00
            virtual_day_start = datetime.datetime.combine(sd.date(), datetime.time(18, 0), tz=tz)
            if sd < virtual_day_start:
                virtual_day_start -= datetime.timedelta(days=1)
           virtual_day_end = virtual_day_start + datetime.timedelta(days=1)
           start_day = virtual_day_start.strftime("%Y-%m-%d")
            if start_day not in daily_sleep:
                daily_sleep[start_day] = []
            if end_day not in daily_sleep:
                daily_sleep[end_day] = []
            # weekday based on start date
            weekday = datetime.datetime.fromisoformat(start_day).weekday()
            stats = sleep["sleep_phase_5_min"]
            # Initialize sleep segments
            sleep_segments = []

            total_duration = ed - sd
            total_seconds = total_duration.total_seconds()
            cumulative_seconds = 0

            # Check for overflow into previous virtual day
            if sd < virtual_day_start:
                dur_prev = virtual_day_start - sd
                if dur_prev.total_seconds() > 0:
                    stats_prev_len = int(len(stats) * (dur_prev.total_seconds() / total_seconds))
                    stats_prev = stats[:stats_prev_len]
                    prev_day = (virtual_day_start - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                    if prev_day not in daily_sleep:
                        daily_sleep[prev_day] = []
                    daily_sleep[prev_day].append({
                        "relative_start": sd - (virtual_day_start - datetime.timedelta(days=1)),
                        "duration": dur_prev,
                        "weekday": weekday,
                        "sleep_stats": stats_prev,
                        "readiness": score,
                        "hr": hr,
                        "hrv": hrv,
                        "temperature": temperature
                    })
                    stats = stats[stats_prev_len:]  # Remove used stats
                    cumulative_seconds += dur_prev.total_seconds()
                    sd = virtual_day_start  # Adjust start time

            # Now compute duration in current virtual day
            dur_current = min(ed, virtual_day_end) - sd
            if dur_current.total_seconds() > 0:
                stats_current_len = int(len(stats) * (dur_current.total_seconds() / (total_seconds - cumulative_seconds)))
                stats_current = stats[:stats_current_len]
                if start_day not in daily_sleep:
                    daily_sleep[start_day] = []
                daily_sleep[start_day].append({
                    "relative_start": sd - virtual_day_start,
                    "duration": dur_current,
                    "weekday": weekday,
                    "sleep_stats": stats_current,
                    "readiness": score,
                    "hr": hr,
                    "hrv": hrv,
                    "temperature": temperature
                })
                stats = stats[stats_current_len:]
                cumulative_seconds += dur_current.total_seconds()
                sd = virtual_day_end  # Adjust start time

# Check for overflow into next virtual day
            if ed > virtual_day_end:
                dur_next = ed - virtual_day_end
                if dur_next.total_seconds() > 0:
                    stats_next = stats  # Remaining stats
                    next_day = virtual_day_end.strftime("%Y-%m-%d")
                    if next_day not in daily_sleep:
                        daily_sleep[next_day] = []
                    daily_sleep[next_day].append({
                        "relative_start": datetime.timedelta(),
                        "duration": dur_next,
                        "weekday": weekday,
                        "sleep_stats": stats_next,
                        "readiness": score,
                        "hr": hr,
                        "hrv": hrv,
                        "temperature": temperature
                    })
        # sort by date
        daily_sleep = dict(sorted(daily_sleep.items(), key=lambda x: x[0]))
        # plot, one large plot that has the sleep data and a small thin plot that shows the hr&hrv data below
        fig, ax = plt.subplots(3, 1, figsize=(15, 10), gridspec_kw={'height_ratios': [6, 1, 1]}, sharex=True)
        # dark mode
        # create horizontal dark gray line at midnight and noon
        ax[0].axhline(y=18, color="#808080", linewidth=1)
        ax[0].axhline(y=18 - 12, color="#808080", linewidth=1)
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
                    ax[0].bar(i, width, bottom=bottom, color="#AAAAAA", width=1, alpha=0.25)
        for i, (day, sleeps) in reversed(list(enumerate(daily_sleep.items()))):
            for sleep in sleeps:
                color = "gray"
                if sleep["readiness"] is not None:
                    color = get_color_hsv(sleep["readiness"] / 100)
                bottom = ((24 * 60 * 60) - sleep[
                    "relative_start"].total_seconds() - sleep["duration"].total_seconds()) / 3600
                width = sleep["duration"].total_seconds() / 3600
                current_bottom = bottom + width
                for state in sleep["sleep_stats"]:
                    current_bottom -= (width / len(sleep["sleep_stats"]))
                    w = 0.9
                    match int(state):
                        case 4:
                            w = 0.4
                        case 3:
                            w = 0.4
                        case 2:
                            w = 0.9
                        case 1:
                            w = 0.9
                    ax[0].bar(i, width / len(sleep["sleep_stats"]), bottom=current_bottom, color=color,
                           alpha=0.2 if state == "4" else 1, width=w)
        # set x axis labels, only every 7th day
        ax[0].set_xticks(range(len(daily_sleep) - 1, 0, -14))
        ax[0].set_xticklabels([day[2:] for i, (day, _) in enumerate(reversed(daily_sleep.items())) if i % 14 == 0])
        # set y axis labels
        ax[0].set_yticks(range(0, 25, 2))
        ax[0].set_yticklabels([f"{i}:00" if i >= 0 else f"{24 + i}:00" for i in range(18, -7, -2)])
        # set y limit
        ax[0].set_ylim(0, 24)
        # set x limit
        ax[0].set_xlim(0, len(daily_sleep))
        # grid
        ax[0].grid(True)
        ax[0].set_axisbelow(True)
        # set title
        ax[0].set_title("Invis's Sleep Schedule")

        x = []
        y_hr = []
        y_hrv = []
        y_temperature = []
        for i, (day, sleeps) in reversed(list(enumerate(daily_sleep.items()))):
            if len(sleeps) == 0:
                continue
            min_hr = min(sleep["hr"] for sleep in sleeps)
            min_hrv = min(sleep["hrv"] for sleep in sleeps)
            max_temperature = max(sleep["temperature"] for sleep in sleeps if sleep["temperature"] is not None)
            x.append(i)
            y_hr.append(min_hr)
            y_hrv.append(min_hrv)
            y_temperature.append(max_temperature)
        # fill the area between the the line and zero
        ax[1].plot(x, y_temperature, color="gray", alpha=0.7)
        # blue area, negative
        ax[1].fill_between(x, y_temperature, color="blue", alpha=0.25, where=[i <= 0 for i in y_temperature], interpolate=True)
        # red area, positive
        ax[1].fill_between(x, y_temperature, color="red", alpha=0.25, where=[i >= 0 for i in y_temperature], interpolate=True)
        ax[2].plot(x, y_hr, color="black", alpha=0.7)
        ax3 = ax[2].twinx()
        ax3.plot(x, y_hrv, color="green", alpha=0.7)
        ax[2].legend(["HR"], loc="lower left")
        ax3.legend(["HRV"], loc="upper left")
        ax[1].legend(["Temperature"], loc="upper left")
        # unit °C on y axis

        # reduce padding
        plt.tight_layout()

        img = BytesIO()
        fig.savefig(img, format='png', dpi=250)
        img.seek(0)
        plt.close()

        e.set_image(url="attachment://sleep.png")
        buf = File(img, filename="sleep.png")
        # send image
        await ctx.send(file=buf, embed=e)

    @hybrid_command()
    async def temperature(self, ctx: Context):
        await ctx.defer(ephemeral=is_hidden(ctx))
        client = Client(cfg["homeassistant.url"], cfg["homeassistant.token"], use_async=True)
        entity = await client.async_get_entity(entity_id="sensor.aranet_4_home_temperature")
        temp = client.async_get_entity_histories(
            entities=[entity],
            start_timestamp=datetime.datetime.now(tz=pytz.timezone("Europe/Vienna")).replace(tzinfo=None) - datetime.timedelta(days=7),
            end_timestamp=datetime.datetime.now(tz=pytz.timezone("Europe/Vienna")).replace(tzinfo=None)
        )
        e = Embed(title="Indoor Temperature Chart")
        # plot
        with plt.rc_context({'font.size': 24}):
            fig, ax = plt.subplots(figsize=(15, 10))
            x = []
            y = []
            async for entity in temp:
                for state in entity.states:
                    try:
                        f = float(state.state)
                    except ValueError:
                        continue
                    x.append(state.last_updated.astimezone(pytz.timezone("Europe/Vienna")))
                    y.append(f)
            # make line thicker
            ax.plot(x, y, linewidth=4)
            #ax.set_ylabel("Temperature")
            #ax.set_xlabel("Time")
            # temp range 15-35°C
            ax.set_ylim(15, 35)
            ax.grid()
            # set x_axis min to x[0] but leave max to None
            ax.set_xlim(x[0], None)
            # format x axis as DD.MM HH:MM
            ax.xaxis.set_major_formatter(
                dates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
            # format y axis as °C
            ax.yaxis.set_major_formatter('{x:.0f}°C')
            # get the color that was used to plot the line
            color = ax.get_lines()[0].get_color()
            # add a big hollow point at the latest point
            ax.scatter(x[-1], y[-1], s=1000, facecolor='none', edgecolor="black", linewidth=4, alpha=0.3)
            # add a vertical line at the latest point
            ax.axvline(x[-1], color="black", linewidth=2, linestyle="--", alpha=0.3)
            # reduce padding
            plt.tight_layout()
            img = BytesIO()
            fig.savefig(img, format='png', dpi=100)
            img.seek(0)
            plt.close()
        e.set_image(url="attachment://temperature.png")
        buf = File(img, filename="temperature.png")
        e.description = f"{y[-1]} °C (as of <t:{x[-1].timestamp():.0f}:R>)"
        # send image
        await ctx.send(file=buf, embed=e)


    # replace get_calendar_data with home assistant variant
    async def get_calendar_data(self):
        client = Client(cfg["homeassistant.url"], cfg["homeassistant.token"], use_async=True)
        work_periods = []
        last_state = None
        async for zone in client.async_get_logbook_entries(
            filter_entities="device_tracker.pixel_8_pro_2",
            start_timestamp=datetime.datetime.now(tz=pytz.timezone("Europe/Vienna")).replace(tzinfo=None) - datetime.timedelta(days=150),
            end_timestamp=datetime.datetime.now(tz=pytz.timezone("Europe/Vienna")).replace(tzinfo=None)
        ):
            state = zone.state
            when = round_minute(zone.when, 10).astimezone(pytz.timezone("Europe/Vienna"))
            if state == "work" and last_state != "work":
                    if work_periods and when - work_periods[-1]["end"] < datetime.timedelta(minutes=30):
                        work_periods[-1]["end"] = when
                    else:
                        work_periods.append({"start": when, "end": when})
            elif last_state == "work":
                work_periods[-1]["end"] = when
            last_state = state
            print(f"zone changed to {last_state} at {when}")
        # it has to have the same format as the old get_calendar_data
        d  = {}
        for period in work_periods:
            sd = period["start"].astimezone(
                pytz.timezone("Europe/Vienna"))
            sd_r = sd + datetime.timedelta(days=1) if sd > sd.replace(hour=18, minute=0, second=0) else sd
            start_day = sd_r.strftime("%Y-%m-%d")
            ed = period["end"].astimezone(
                pytz.timezone("Europe/Vienna"))
            ed_r = ed + datetime.timedelta(days=1) if ed > ed.replace(hour=18, minute=0, second=0) else ed
            end_day = ed_r.strftime("%Y-%m-%d")

            if start_day not in d:
                d[start_day] = []
            if end_day not in d:
                d[end_day] = []
            thresh = datetime.datetime(year=period["end"].year, month=period["end"].month, day=period["end"].day, hour=18,
                                       tzinfo=period["end"].tzinfo)
            if start_day != end_day:
                dur_first = thresh - period["start"]
                if dur_first >= datetime.timedelta(hours=24):
                    dur_first -= datetime.timedelta(hours=24)
                dur_second = period["end"] - thresh
                if dur_second <= datetime.timedelta(hours=0):
                    dur_second += datetime.timedelta(hours=24)
                total_dur = dur_first + dur_second
                # split stats into two parts based on duration of each part
                d[start_day].append(
                    {"relative_start": period["start"] - (thresh - datetime.timedelta(days=1)), "duration": dur_first})
                d[end_day].append(
                    {"relative_start": datetime.timedelta(), "duration": dur_second})
            else:
                relative_start = period["start"] - (thresh - datetime.timedelta(days=1))
                if relative_start >= datetime.timedelta(hours=24):
                    relative_start -= datetime.timedelta(hours=24)
                d[start_day].append(
                    {"relative_start": relative_start, "duration": period["end"] - period["start"]})
        return d

def round_minute(date: datetime = None, round_to: int = 1):
    """
    round datetime object to minutes
    """
    if not date:
        date = datetime.datetime.now()
    minute = round(date.minute / round_to) * round_to
    date = date.replace(minute=0, second=0, microsecond=0)
    return date + datetime.timedelta(minutes=minute)

async def setup(bot):
    await bot.add_cog(Oura(bot))
