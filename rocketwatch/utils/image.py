import math
from enum import Enum
from io import BytesIO
from functools import cache
from typing import Optional

from discord import File
from PIL import ImageFont, Image as PillowImage
from PIL.ImageDraw import ImageDraw


Color = tuple[int, int, int]

class Image:
    def __init__(self, image: PillowImage.Image):
        self.__img = image

    def to_file(self, name: str) -> File:
        buffer = BytesIO()
        self.__img.save(buffer, format="png")
        buffer.seek(0)
        return File(buffer, name)


class Font(str, Enum):
    INTER = "Inter"


class FontVariant(str, Enum):
    REGULAR = "Regular"
    BOLD = "Bold"


class ImageCanvas(ImageDraw):
    # default color matches Discord mobile dark mode Embed
    def __init__(self, width: int, height: int, bg_color: Color = (37, 39, 26)):
        p_img = PillowImage.new('RGB', (width, height), color=bg_color)
        super().__init__(p_img)
        self.image = Image(p_img)

    def progress_bar(
            self,
            xy: tuple[float, float],
            size: tuple[float, float],
            progress: float,
            fill_color: Color,
            bg_color : Color = (0, 0, 0)
    ) -> None:
        x, y = xy
        width, height = size
        if width <= height:
            raise ValueError("Progress bar must be wider than it is tall")

        radius = height / 2
        self.rounded_rectangle((x, y, x + width, y + height), radius, bg_color)

        fill_width = progress * width
        if fill_width > 0:
            # left semicircle
            fill_perc = min(1.0, fill_width / radius)
            angle = 90 * (1 + 2 * math.acos(fill_perc) / math.pi)
            self.chord((x, y, x + 2 * radius, y + height), angle, 360 - angle, fill_color)

        if fill_width > radius:
            # main bar
            self.rectangle((x + radius, y, x + min(fill_width, width - radius), y + height), fill_color)

        if fill_width > (width - radius):
            # right semicircle
            fill_perc = min(1.0, (fill_width - width + radius) / radius)
            angle = 90 * (2 * math.acos(fill_perc) / math.pi)
            self.chord((x + width - 2 * radius, y, x + width, y + height), angle, 360 - angle, fill_color)

    @cache
    def _get_font(self, name: str, variant: FontVariant, size: float) -> ImageFont:
        return ImageFont.truetype(f"fonts/{name}-{variant}.ttf", size)

    def dynamic_text(
            self,
            xy: tuple[float, float],
            text: str,
            font_size: float,
            font_name: Font = Font.INTER,
            font_variant: FontVariant = FontVariant.REGULAR,
            color: Color = (255, 255, 255),
            max_width: Optional[float] = None,
            anchor: str = "lt"
    ) -> None:
        font = self._get_font(font_name, font_variant, font_size)
        if max_width is not None:
            # cut off the text if it's too long
            while text and (font.getbbox(text)[2] > max_width):
                # replace last character with an ellipsis
                text = f"{text[:-2]}…"

        self.text(xy, text, font=font, fill=color, anchor=anchor)
