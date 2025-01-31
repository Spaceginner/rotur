import logging

import rotur.backend
import rotur.handlers


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    backend = rotur.backend.BackendAPI(designator="ori")
    account: rotur.handlers._account._AccountManager = backend.attach(rotur.handlers.AccountManagerBuilder(agent=rotur.handlers.ClientAgent.origin_os()))

    account.login("Spaceginner", "Space")

    print(backend.room_members)
    print(account.user.data["sys.currency"])


if __name__ == '__main__':
    main()
