# 30.07.26

import logging

from rich.console import Console

from VibraVid.utils import config_manager

console = Console()
logger = logging.getLogger(__name__)

ANONYMOUS = "Anonymous"
ACCOUNT = "Account"
DEVICE = "Device"


def _get_me_enabled() -> bool:
    """
    Whether services may spend a request to name the logged-in account (DEFAULT.get_me).

    Off by default: the banner's account type costs nothing, but resolving the name means an extra
    authenticated round trip per process, and that is a cost the user should opt into.
    """
    return config_manager.config.get_bool("DEFAULT", "get_me", default=False)


def print_login(auth_type: str, user: str = "", resolver=None) -> None:
    """
    Print the shared two-line login banner.

    Args:
        auth_type: One of ANONYMOUS / ACCOUNT / DEVICE.
        user: Account name already in hand, if any. Skips the resolver entirely.
        resolver: Zero-arg callable returning the account name, used only when `user` is empty
                  and the session is not anonymous.
    """
    if not user and resolver is not None and auth_type != ANONYMOUS and _get_me_enabled():
        try:
            user = resolver() or ""
        except Exception as e:
            logger.debug(f"Could not resolve the account name: {e}")
            user = ""

    # Emitted as a single print so the two lines can never be split apart by concurrent output.
    line = f"[cyan]Login - Type: [green]{auth_type}"
    if user:
        line += f"\n[cyan]  User: [green]{user}"
    console.print(line)
