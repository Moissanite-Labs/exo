"""A bounded memo for rendered context text; eviction never removes the durable source."""
from collections import OrderedDict


class TextCache:
    def __init__(self, max_entries: int = 256, max_characters: int = 1_048_576):
        self.max_entries = max_entries
        self.max_characters = max_characters
        self.characters = 0
        self.values: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        if value is not None:
            self.values.move_to_end(key)
        return value

    def put(self, key: str, value: str) -> None:
        old = self.values.pop(key, None)
        if old is not None:
            self.characters -= len(old)
        if self.max_entries <= 0 or len(value) > self.max_characters:
            return
        self.values[key] = value
        self.characters += len(value)
        while len(self.values) > self.max_entries or self.characters > self.max_characters:
            _, removed = self.values.popitem(last=False)
            self.characters -= len(removed)
