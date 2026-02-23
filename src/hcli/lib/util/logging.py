import json


class StructuredMessage:
    def __init__(self, message, /, *args, **kwargs):
        self.message = message
        self.args = args
        self.kwargs = kwargs

    def __str__(self):
        message = self.message % self.args
        return "{} <structured: {}>".format(message, json.dumps({"message": message} | self.kwargs))


m = StructuredMessage  # optional, to improve readability
