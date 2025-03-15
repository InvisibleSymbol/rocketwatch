from enum import Enum
from io import BytesIO
from functools import cache
from typing import Optional, Literal

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
    # default color matches Discord Desktop dark mode Embed color (#2b2d31)
    def __init__(self, width: int, height: int, bg_color: Color = (43, 45, 49)):
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
        height, width = size
        if width <= 2 * height:
            raise ValueError("Progress bar width must be at least twice its height")

        radius = height / 2
        x0 = x + radius
        x1 = x + width - radius

        self.circle((x0, y + radius), radius, fill=bg_color)
        if progress > 0:
            self.circle((x0, y + radius), radius, fill=fill_color)

        self.rectangle((x0, y, x1, y + height), fill=bg_color)
        self.circle((x1, y + radius), radius, fill=bg_color)

        x1 = x + round(progress * width) - radius
        if x1 >= x0:
            self.rectangle((x0, y, x1, y + height), fill=fill_color)
        if progress == 1:
            self.circle((x1, y + radius), radius, fill=fill_color)

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
