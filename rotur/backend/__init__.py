import collections
import typing as t
import abc
import random
import string

from .raw import RawBackendAPI
from .schema import ClientMetadata, JsonValue, Status, UListMode


class PmsgHandlerBuilderI[F](abc.ABC):
    @abc.abstractmethod
    def finalise(self, send_callback: t.Callable[[str, JsonValue], Status], session_id: str) -> tuple[F, str | None, t.Callable[[str, JsonValue], None]]: ...


class _PmsgHandlerManager:
    handlers: collections.defaultdict[str | None, list[t.Callable[[str, JsonValue], None]]]

    def __init__(self) -> None:
        self.handlers = collections.defaultdict(list)

    def register(self, for_: str | None, handler: t.Callable[[str, JsonValue], None]) -> None:
        self.handlers[for_].append(handler)

    def handle(self, id_: str, payload: JsonValue) -> None:
        for callback in (self.handlers[id_] + self.handlers[None]):
            callback(id_, payload)


class BackendAPI:
    _raw: RawBackendAPI

    designator: str
    session_name: str

    # xxx should it be a set?
    _room_members: list[str]

    _pmsg_handlers: _PmsgHandlerManager

    @staticmethod
    def _gen_str(len_: int) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=len_))

    def _gen_session_name(self, base: str | None) -> str:
        if base is not None:
            return f"{self.designator}-{base}§{self._gen_str(10)}"
        else:
            return f"{self.designator}-{self._gen_str(32)}"

    def __init__(
            self,
            url: str = "wss://rotur.mistium.com/",
            *,
            base_name: str | None = None,
            designator: str = "pyapi",
            client: ClientMetadata = ClientMetadata.python_api(),
    ) -> None:
        self.designator = designator

        self.session_name = self._gen_session_name(base_name)

        self._raw = RawBackendAPI(url)

        self._pmsg_handlers = _PmsgHandlerManager()
        self._raw.on_ulist.connect(self._on_ulist_update)
        self._raw.on_pmsg.connect(self._on_pmsg)

        if not self._raw.handshake(client).is_ok:
            raise RuntimeError("handshake failed")

        if not self._raw.set_id(self.session_name)[0].is_ok:
            raise RuntimeError("announcing id failed")

        if not self._raw.link(["roturTW"]).is_ok:
            raise RuntimeError("linking to room failed")

    def close(self) -> None:
        self._raw.close()

    @t.overload
    def _on_ulist_update(self, mode: t.Literal[UListMode.SET], users: list[str]) -> None: ...
    @t.overload
    def _on_ulist_update(self, mode: t.Literal[UListMode.ADD] | t.Literal[UListMode.REMOVE], user: str) -> None: ...

    def _on_ulist_update(self, mode: UListMode, users: str | list[str]) -> None:
        match mode:
            case UListMode.SET:
                self._room_members = users
            case UListMode.ADD:
                self._room_members.append(users)
            case UListMode.REMOVE:
                self._room_members = [m for m in self._room_members if m != users]

    @property
    def room_members(self) -> t.Sequence[str]:
        return self._room_members

    def _on_pmsg(self, from_: str, msg: JsonValue) -> None:
        self._pmsg_handlers.handle(from_, msg)

    def attach[F](self, handler_builder: PmsgHandlerBuilderI[F]) -> F:
        handler, handled_id, handler_cb = handler_builder.finalise(self._raw.send_msg, self.session_name)

        self._pmsg_handlers.register(handled_id, handler_cb)

        return handler
