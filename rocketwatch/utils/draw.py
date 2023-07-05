# Open the image and do stuff
# I tested this with a blank 800x400 RGBA
from PIL import ImageFont
from PIL.ImageDraw import ImageDraw


class BetterImageDraw(ImageDraw):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fonts_cache = {}

    def progress_bar(self, xy, size, progress, primary=(211, 211, 211), secondary=(15, 15, 15)):
        x,y = xy
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

    def _get_font(self, font_size):
        font = self._fonts_cache.get(font_size)
        if font is None:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
            self._fonts_cache[font_size] = font
        return font

    def dynamic_text(self, xy, text, font_size, color=(211, 211, 211), max_width=None, anchor="lt"):
        font = self._get_font(font_size)
        if max_width is not None:
            # cut of the text if it's too long
            while font.getbbox(text)[2] > max_width and text:
                text = text[:-1]
                # replace last character with an ellipsis
                text = f"{text[:-1]}…"
        self.text(xy, text, font=font, fill=color, anchor=anchor)

