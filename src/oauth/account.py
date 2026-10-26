"""WorkBuddy account metadata enrichment, separate from the login protocol."""

import logging

import httpx

from ..credential_utils import first_claim

logger = logging.getLogger(__name__)

FIELDS = {
    "account_uid": ("uid", "userId", "user_id", "id"),
    "phone": ("phone", "mobile", "mobilePhone", "phoneNumber", "telephone"),
    "account_name": ("name", "nickname"),
    "account_email": ("email",),
}


async def lookup_workbuddy_account(
    client, base, host, auth_state, access_token, user_agent
):
    try:
        response = await client.get(
            base + "/v2/plugin/login/account",
            params={"state": auth_state},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": user_agent,
                "X-Product": "WorkBuddy",
                "X-Domain": host,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {}
        sources = [data]
        if isinstance(data.get("account"), dict):
            sources.append(data["account"])
        result = {}
        for field, aliases in FIELDS.items():
            for source in sources:
                value = first_claim(source, aliases)
                if value:
                    result[field] = value
                    break
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.debug("Account metadata lookup failed: %s", type(exc).__name__)
        return {}
