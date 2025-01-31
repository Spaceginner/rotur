import dataclasses
import enum
import typing as t


@dataclasses.dataclass(frozen=True)
class Status:
    code: int
    msg: str

    @property
    def is_ok(self):
        return self.code == 100


class UListMode(enum.StrEnum):
    SET = 'set'
    ADD = 'add'
    REMOVE = 'remove'


@dataclasses.dataclass(frozen=True)
class ClientMetadata:
    language: str
    editor: str
    version: int

    @classmethod
    def default(cls) -> t.Self:
        return cls("Python", "RoturAPI", 1)

    @classmethod
    def origin_os(cls) -> t.Self:
        return cls("Scratch", "TurboWarp", 2)


type JsonValue = int | float | str | t.Sequence[JsonValue] | t.Mapping[JsonValue, JsonValue] | None
