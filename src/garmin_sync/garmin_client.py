"""Thin wrapper around the upstream garminconnect client."""

from __future__ import annotations

from collections.abc import Callable

from garminconnect import Garmin

from garmin_sync.config import AccountConfig


def login(account: AccountConfig, mfa_prompt: Callable[[str], str] | None = None) -> Garmin:
    """Create and authenticate a Garmin client for one account."""

    account.tokenstore.parent.mkdir(parents=True, exist_ok=True)
    client = Garmin(
        account.email,
        account.password,
        is_cn=account.is_cn,
        return_on_mfa=True,
    )
    status, continuation = client.login(str(account.tokenstore))

    if status == "needs_mfa":
        if mfa_prompt is None:
            mfa_prompt = input
        code = mfa_prompt("Garmin MFA code: ").strip()
        client.resume_login(continuation, code)

    client.client.dump(str(account.tokenstore))
    return client
