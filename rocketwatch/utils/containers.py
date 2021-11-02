class Response:
    def __init__(self, embed=None, file=None, event_name=""):
        self.embed = embed
        self.file = file
        self.event_name = event_name

    def __bool__(self):
        return bool(self.embed)
