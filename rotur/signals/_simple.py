import enum
import typing as t


class CallbackType(enum.Enum):
    PERSISTENT = enum.auto()
    ONE_SHOT = enum.auto()


class SimpleSignal[**CBP]:
    callbacks: list[tuple[t.Callable[CBP, None], CallbackType]]

    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback: t.Callable[CBP, None], type_: CallbackType = CallbackType.PERSISTENT) -> None:
        self.callbacks.append((callback, type_))

    def emit(self, *args: CBP.args, **kwargs: CBP.kwargs) -> None:
        for callback in self.callbacks:
            callback[0](*args, **kwargs)

        self.callbacks = [cb for cb in self.callbacks if cb[1] == CallbackType.PERSISTENT]
