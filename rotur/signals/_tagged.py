import typing as t


class TaggedSignal[**CBP]:
    last_tag: int
    callbacks: dict[int, t.Callable[CBP, None]]

    def __init__(self) -> None:
        self.last_tag = 0
        self.callbacks = {}

    def _get_next_tag(self) -> int:
        self.last_tag += 1
        return self.last_tag

    def connect(self, callback: t.Callable[CBP, None]) -> int:
        self.callbacks[tag_ := self._get_next_tag()] = callback
        return tag_

    def emit(self, tag_: int, *args: CBP.args, **kwargs: CBP.kwargs) -> None:
        self.callbacks.pop(tag_)(*args, **kwargs)
