from io import BytesIO
from typing import Optional

from PIL import ImageFont
from PIL import Image as PillowImage
from PIL.ImageDraw import ImageDraw
from discord import File

Color = tuple[int, int, int]

class Image:
    def __init__(self, image: PillowImage.Image):
        self.__img = image

    def to_file(self, name: str) -> File:
        buffer = BytesIO()
        self.__img.save(buffer, format="png")
        buffer.seek(0)
        return File(buffer, name)

class ImageCanvas(ImageDraw):
    # default color matches Discord Desktop dark mode Embed color (#2b2d31)
    def __init__(self, width: int, height: int, bg_color: Color = (43, 45, 49)):
        p_img = PillowImage.new('RGB', (width, height), color=bg_color)
        super().__init__(p_img)
        self.image = Image(p_img)
        self._fonts_cache: dict[int, ImageFont] = {}

    def progress_bar(
            self,
            xy: tuple[int, int],
            size: tuple[int, int],
            progress: float,
            primary: Color = (211, 211, 211),
            secondary : Color = (15, 15, 15)
    ) -> None:
        x, y = xy
        height, width = size
        # Draw the background
        self.rectangle((x + (height / 2), y, x + width + (height / 2), y + height), fill=secondary, width=10)
        self.ellipse((x + width, y, x + height + width, y + height), fill=secondary)
        self.ellipse((x, y, x + height, y + height), fill=secondary)
        width = int(width * progress)
        # Draw the part of the progress bar that is actually filled
        self.rectangle((x + (height / 2), y, x + width + (height / 2), y + height), fill=primary, width=10)
        self.ellipse((x + width, y, x + height + width, y + height), fill=primary)
        self.ellipse((x, y, x + height, y + height), fill=primary)

    def _get_font(self, font_size: int) -> ImageFont:
        if font_size not in self._fonts_cache:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
            self._fonts_cache[font_size] = font
        return self._fonts_cache[font_size]

    def dynamic_text(
            self,
            xy: tuple[int, int],
            text: str,
            font_size: int,
            color: Color = (211, 211, 211),
            max_width: Optional[int] = None,
            anchor: str = "lt"
    ):
        font = self._get_font(font_size)
        if max_width is not None:
            # cut off the text if it's too long
            while font.getbbox(text)[2] > max_width and text:
                text = text[:-1]
                # replace last character with an ellipsis
                text = f"{text[:-1]}…"
        self.text(xy, text, font=font, fill=color, anchor=anchor)
