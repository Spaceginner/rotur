import dataclasses
import hashlib
import threading
import time
import typing as t

from .._logger import logger as glob_logger
from ..backend import PmsgHandlerBuilderI, JsonValue, Status


logger = glob_logger.getChild("accmgr")


class SingleSignal[**CBP]:
    callback: t.Callable[CBP, None] | None
    _free: threading.Event

    def __init__(self) -> None:
        self.callback = None
        self._free = threading.Event()
        self._free.set()

    def connect(self, callback: t.Callable[CBP, None]) -> None:
        self._free.wait()
        self._free.clear()

        assert self.callback is None

        self.callback = callback

    def clear(self) -> None:
        self.callback = None
        self._free.set()

    def emit(self, *args: CBP.args, **kwargs: CBP.kwargs) -> None:
        if self.callback is None:
            raise RuntimeError("there is no callback")

        self.callback(*args, **kwargs)
        self.callback = None

        self._free.set()


class FailingSignal[**SCBP, **FCBP]:
    _succ: SingleSignal[SCBP]
    _fail: SingleSignal[FCBP]

    def __init__(self) -> None:
        self._succ = SingleSignal()
        self._fail = SingleSignal()

    def connect(self, succ_cb: t.Callable[SCBP, None], fail_cb: t.Callable[FCBP, None]) -> None:
        self._succ.connect(succ_cb)
        self._fail.connect(fail_cb)

    def succed(self, *args: SCBP.args, **kwargs: SCBP.kwargs) -> None:
        self._succ.emit(*args, **kwargs)
        self._fail.clear()

    def failed(self, *args: FCBP.args, **kwargs: FCBP.kwargs) -> None:
        self._fail.emit(*args, **kwargs)
        self._succ.clear()

    def clear(self) -> None:
        self._succ.clear()
        self._fail.clear()


@dataclasses.dataclass(frozen=True)
class ClientAgent:
    system: str
    version: str

    @classmethod
    def python_api(cls) -> t.Self:
        return cls("PyRoturAPI", "0.0.1")

    @classmethod
    def origin_os(cls) -> t.Self:
        return cls("originOS", "v5.5.1")


@dataclasses.dataclass
class _UserStorage:
    _inner: t.MutableMapping[str, JsonValue]

    _on_set: t.Callable[[str, JsonValue], None]

    def _update(self, key: str, value: JsonValue) -> None:
        if value is None:
            del self._inner[key]
        else:
            self._inner[key] = value

    def __setitem__(self, key: str, value: JsonValue) -> None:
        if value is None:
            raise ValueError("cannot set value to None")

        self._on_set(key, value)
        self._inner[key] = value

    def __delitem__(self, key: str) -> None:
        self._on_set(key, None)
        del self._inner[key]

    def __getitem__(self, key: str) -> JsonValue:
        return self._inner[key]

    def __str__(self) -> str:
        return self._inner.__str__()

    def __repr__(self) -> str:
        return self._inner.__repr__()

    def __format__(self, format_spec: str) -> str:
        return self._inner.__format__(format_spec)


@dataclasses.dataclass
class _User:
    _name: str
    data: _UserStorage
    _is_first_login: bool
    _token: str

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_first_login(self) -> bool:
        return self._is_first_login

    @property
    def token(self) -> str:
        return self._token

    def storage(self, id_: str) -> t.Never:
        raise NotImplementedError


@dataclasses.dataclass
class _AccountManager:
    session_id: str
    account_server_id: str
    agent: ClientAgent
    _send_raw: t.Callable[[str, JsonValue], Status]

    _on_r_login: FailingSignal[
        [bool, str, t.Mapping[JsonValue, JsonValue]],
        [str]
    ] = dataclasses.field(init=False, default_factory=FailingSignal)
    """args: 
    <succ> is first login, token, data;
    <fail> reason"""
    _on_r_create_account: FailingSignal[
        [],
        [str],
    ] = dataclasses.field(init=False, default_factory=FailingSignal)
    """args:
    <succ> <none>;
    <fail> reason"""
    _on_r_update: FailingSignal[
        [],
        [str],
    ] = dataclasses.field(init=False, default_factory=FailingSignal)
    """args:
    <succ> <none>;
    <fail> reason"""
    _on_r_delete_account: FailingSignal[
        [],
        [str],
    ] = dataclasses.field(init=False, default_factory=FailingSignal)
    """args:
    <succ> <none>;
    <fail> reason"""

    user: _User | None = dataclasses.field(init=False, default=None)

    def _handle_msg(self, _from: str, payload: JsonValue) -> None:
        try:
            match payload["source_command"]:
                case "login":
                    if isinstance(payl := payload["payload"], str):
                        self._on_r_login.failed(payl)
                    else:
                        self._on_r_login.succed(payload["first_login"], payload["token"], payload["payload"])
                case "New_Account":
                    if payload == "Account Created Successfully":
                        self._on_r_create_account.succed()
                    else:
                        self._on_r_create_account.failed(payload)
                case "Update":
                    if payload == "Account Updated Successfully":
                        self._on_r_update.succed()
                    else:
                        self._on_r_update.failed(payload)
                case "account_update":
                    if (v := payload["value"]) is not None:
                        self.user.data._inner[payload["key"]] = v
                    else:
                        del self.user.data._inner[payload["key"]]
                case src_cmd:
                    logger.warning(f"received an unknown/unimplemented command: {src_cmd}")
        except KeyError:
            logger.warning("received a malformed msg")
            return
        except RuntimeError:
            logger.warning("received an unexpected response msg")
            return

    @staticmethod
    def _gen_timestamp() -> int:
        return round(time.time())

    def _send[**SCBP, **FCBP](
            self,
            command: str,
            r_signal: FailingSignal[SCBP, FCBP] | None,
            p: JsonValue | None,
            /,
            r_cb: tuple[
                t.Callable[SCBP, None] | None,
                t.Callable[FCBP, None] | None
            ] = (None, None),
    ) -> None:
        def no_cb(*_args, **_kwargs) -> None:
            pass

        if r_signal is not None:
            r_signal.connect(r_cb[0] or no_cb, r_cb[1] or no_cb)

        payload = {
            "command": command,
            "id": self.user.token if self.user is not None else self.session_id,
            "client": dataclasses.asdict(self.agent),
            "timestamp": self._gen_timestamp(),
        }

        if p is not None:
            payload["payload"] = p

        resp = self._send_raw(
            self.account_server_id,
            payload
        )

        if not resp.is_ok:
            # todo fail callback
            logger.error("received an error response when sending pmsg")
            r_signal.clear()

    @t.overload
    def _fetch[**SCBP, **FCBP](
            self,
            command: str,
            r_signal: FailingSignal[SCBP, FCBP],
            p: JsonValue,
    ) -> tuple[t.Literal[True], SCBP.args]: ...

    @t.overload
    def _fetch[**SCBP, **FCBP](
            self,
            command: str,
            r_signal: FailingSignal[SCBP, FCBP],
            p: JsonValue,
    ) -> tuple[t.Literal[False], FCBP.args]: ...

    def _fetch[**SCBP, **FCBP](
            self,
            command: str,
            r_signal: FailingSignal[SCBP, FCBP] | None,
            p: JsonValue | None,
    ) -> tuple[bool, t.Union[SCBP.args, FCBP.args]]:
        data: t.Union[SCBP.args, FCBP.args, None] = None
        success = True
        responded = threading.Event()

        def s_cb(*args: SCBP.args, **kwargs: SCBP.kwargs) -> None:
            nonlocal data, responded

            if kwargs:
                raise RuntimeError("fetch does not support kwargs")

            data = args
            responded.set()

        def f_cb(*args: FCBP.args, **kwargs: FCBP.kwargs) -> None:
            nonlocal success

            success = False

            s_cb(*args, **kwargs)  # type: ignore

        self._send(
            command,
            r_signal,
            p,
            r_cb=(s_cb, f_cb),
        )

        responded.wait()

        assert data is not None

        return success, data

    @staticmethod
    def _hash_pwd(pwd: str) -> str:
        return hashlib.md5(pwd.encode()).hexdigest()

    def _update_acc_data(self, key: str, value: JsonValue) -> None:
        is_succ, resp = self._fetch(
            "Update",
            self._on_r_update,
            [key, value]
        )

        if not is_succ:
            raise ValueError("updating account has failed", resp)

    def login(self, username: str, password: str) -> None:
        if self.user is not None:
            raise RuntimeError("already logged in")

        is_succ, resp = self._fetch(
            "login",
            self._on_r_login,
            [username, self._hash_pwd(password)]
        )

        if is_succ:
            is_first_login, token, data = resp

            self.user = _User(username, _UserStorage(data, self._update_acc_data), is_first_login, token)
        else:
            raise ValueError("login has failed", resp)

    def logout(self) -> None:
        if self.user is None:
            raise RuntimeError("already logged out")

        self._fetch(
            "logout",
            None,
            None
        )

        self.user = None

    def create_acc(self, username: str, password: str) -> None:
        is_succ, resp = self._fetch(
            "New_Account",
            self._on_r_create_account,
            {
                "username": username,
                "password": self._hash_pwd(password),
            }
        )

        if not is_succ:
            raise ValueError("creating account has failed", resp)

    def ensure_login(self, username: str, password: str) -> None:
        """relogin if already logged in, create user if such user doesn't exist yet"""
        try:
            self.login(username, password)
        except ValueError:
            self.create_acc(username, password)
            self.ensure_login(username, password)
        except RuntimeError:
            self.logout()
            self.ensure_login(username, password)

    def delete_acc(self) -> None:
        if self.user is None:
            raise RuntimeError("not logged in")

        is_succ, resp = self._fetch(
            "delete_account",
            self._on_r_delete_account,
            None
        )

        if not is_succ:
            raise ValueError("deleting the account has failed", resp)

    def send_friend_request(self, to: str) -> None:
        raise NotImplementedError

    def accept_friend_request(self, from_: str) -> None:
        raise NotImplementedError

    def transfer_funds(self, amount: float, to: str) -> None:
        raise NotImplementedError

    def buy_item(self, id_: str) -> None:
        raise NotImplementedError

    def available_items(self) -> list[str]:
        raise NotImplementedError

    def get_mail(self) -> list:
        raise NotImplementedError

    def send_mail(self, to: str, contents: str) -> None:
        raise NotImplementedError

    def get_badges(self) -> list[str]:
        raise NotImplementedError


@dataclasses.dataclass(kw_only=True)
class AccountManagerBuilder(PmsgHandlerBuilderI):
    account_server_id: str = "sys-rotur"  # todo auto-fetch
    agent: ClientAgent = ClientAgent.python_api()
    user_token: str | None = None

    def finalise(
            self,
            send_callback: t.Callable[[str, JsonValue], Status],
            session_id: str) -> tuple[_AccountManager, str | None, t.Callable[[str, JsonValue], None]]:
        return (
            (mgr := _AccountManager(session_id, self.account_server_id, self.agent, send_callback)),
            mgr.account_server_id,
            mgr._handle_msg,
        )
