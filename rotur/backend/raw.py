import json
import queue
import threading
import typing as t

from websockets.sync import client as ws

from .._logger import logger
from ..signals import SimpleSignal, TaggedSignal
from .schema import Status, UListMode, ClientMetadata, JsonValue

__all__ = ['RawBackendAPI']


class RawBackendAPI:
    _ws: ws.ClientConnection
    _ws_l_worker: threading.Thread
    _ws_h_worker: threading.Thread
    _ws_queue: queue.Queue[str]

    # should have been part of handshake response smh
    on_client_ip: SimpleSignal[[str]]
    """args: ip"""
    on_server_version: SimpleSignal[[str]]
    """args: version"""

    on_pmsg: SimpleSignal[[str, JsonValue]]
    """args: from, payload"""
    on_ulist: SimpleSignal[[UListMode, str | list[str]]]  # todo properly type
    """args: mode, username if add/remove, else list[username] if set"""

    # todo error handling
    _on_r_handshake: TaggedSignal[[Status]]
    """args: status"""
    _on_r_setid: TaggedSignal[[Status, tuple[str, str] | None]]
    """args: status, (room, username)?"""
    _on_r_link: TaggedSignal[[Status]]
    """args: status"""
    _on_r_pmsg: TaggedSignal[[Status]]
    """args: status"""

    def __init__(self, url: str) -> None:
        self._ws = ws.connect(url)

        self.on_pmsg = SimpleSignal()
        self.on_ulist = SimpleSignal()
        self.on_client_ip = SimpleSignal()
        self.on_server_version = SimpleSignal()

        self._on_r_handshake = TaggedSignal()
        self._on_r_setid = TaggedSignal()
        self._on_r_link = TaggedSignal()
        self._on_r_pmsg = TaggedSignal()

        self._ws_queue = queue.Queue()
        self._ws_l_worker = threading.Thread(target=self._ws_listener)
        self._ws_h_worker = threading.Thread(target=self._ws_handler)

        # xxx should it be a separate method?
        self._ws_h_worker.start()
        self._ws_l_worker.start()

    # todo into more proper context manager api
    def close(self) -> None:
        self._ws.close()

    def _ws_listener(self) -> None:
        for msg in self._ws:
            if not isinstance(msg, str):
                logger.warning("received non-text websocket packet")
                continue

            logger.debug(f"received: {msg!r}")
            self._ws_queue.put(msg)

    def _ws_handler(self) -> None:
        # todo type scheme verification
        for ws_msg in iter(self._ws_queue.get, None):
            try:
                msg = json.loads(ws_msg)
            except json.JSONDecodeError:
                logger.warning("received invalid packet")
                continue

            try:
                cmd = msg["cmd"]
            except KeyError:
                logger.warning("received a packet without command")
                continue

            try:
                match cmd:
                    case "statuscode":
                        try:
                            [command, id_s] = msg["listener"].rsplit("#", 1)
                        except KeyError:
                            logger.warning("received a statuscode with no listener")
                            continue
                        except ValueError:
                            logger.warning(f"received a statuscode with invalid listener tag: {msg["listener"]}")
                            continue

                        try:
                            id_ = int(id_s)
                        except ValueError:
                            logger.warning(f"received a statuscode with invalid listener id: {id_s}")
                            continue

                        status = Status(msg["code_id"], msg["code"])

                        try:
                            match command:
                                case "handshake":
                                    self._on_r_handshake.emit(id_, status)
                                case "setid":
                                    if status.is_ok:
                                        try:
                                            room = msg["val"]["room"]
                                            username = msg["val"]["username"]
                                        except KeyError:
                                            logger.warning(f"received a statuscode with malformed scheme: {msg}")
                                            continue

                                        self._on_r_setid.emit(id_, status, (room, username))
                                    else:
                                        self._on_r_setid.emit(id_, status, None)
                                case "link":
                                    self._on_r_link.emit(id_, status)
                                case "pmsg":
                                    self._on_r_pmsg.emit(id_, status)
                                case command:
                                    logger.warning(f"received a statuscode to an unknown listener: {command}")
                        except KeyError:
                            logger.warning(f"received a statuscode to a non-existent listener: {msg["listener"]}")
                            continue
                    case "ulist":
                        try:
                            mode = UListMode(msg["mode"])
                        except ValueError:
                            logger.warning(f"received malformed ulink cmd: unknown mode {msg["mode"]}")
                            continue

                        match mode:
                            case UListMode.SET as mode:
                                self.on_ulist.emit(mode, [u["username"] for u in msg["val"]])
                            case (UListMode.ADD | UListMode.REMOVE) as mode:
                                self.on_ulist.emit(mode, msg["val"]["username"])
                    case "pmsg":
                        self.on_pmsg.emit(msg["origin"]["username"], msg["val"])
                    case "client_ip":
                        self.on_client_ip.emit(msg["val"])
                    case "server_version":
                        self.on_server_version.emit(msg["val"])
                    case cmd:
                        logger.warning(f"received unknown command: {cmd}")
            except KeyError:
                logger.warning(f"received a {cmd} with malformed scheme: {msg}")

    def _send[**CBP](
            self,
            command: str,
            r_signal: TaggedSignal[CBP],
            /,
            r_cb: t.Callable[CBP, None] | None = None,
            p: JsonValue | None = None,
            rpu: JsonValue | None = None,
    ) -> None:
        def cb(*_args: CBP.args, **_kwargs: CBP.kwargs) -> None:
            pass

        listener_tag = r_signal.connect(r_cb or cb)

        payload = {
            "cmd": command,
            "listener": f"{command}#{listener_tag}"
        }

        if p is not None:
            payload["val"] = p  # type: ignore

        if rpu is not None:
            payload.update(rpu)  # type: ignore

        ws_p = json.dumps(payload)
        logger.debug(f"sending: {ws_p!r}")
        self._ws.send(ws_p)

    def _fetch[**CBP](
            self,
            command: str,
            r_signal: TaggedSignal[CBP],
            p: JsonValue,
            /,
            rpu: JsonValue | None = None
    ) -> CBP.args:
        data = None
        handled = threading.Event()

        def cb(*args: CBP.args, **kwargs: CBP.kwargs) -> None:
            nonlocal data

            if kwargs:
                raise RuntimeError("fetch does not support kwargs")

            data = args
            handled.set()

        self._send(command, r_signal, r_cb=cb, p=p, rpu=rpu)

        handled.wait()

        assert data is not None

        return data

    def handshake(self, client: ClientMetadata) -> Status:
        return self._fetch(
            "handshake",
            self._on_r_handshake,
            {
                "language": client.language,
                "version": {
                    "editorType": client.editor,
                    "version": client.version,
                },
            }
        )[0]

    def set_id(self, id_: str) -> tuple[Status, tuple[str, str] | None]:
        """returns: room, username"""

        return self._fetch(
            "setid",
            self._on_r_setid,
            id_
        )

    def link(self, rooms: list[str]) -> Status:
        return self._fetch(
            "link",
            self._on_r_link,
            rooms,
        )[0]

    def send_msg(self, to: str, payload: JsonValue) -> Status:
        return self._fetch(
            "pmsg",
            self._on_r_pmsg,
            payload,
            rpu={
                "id": to
            }
        )[0]
