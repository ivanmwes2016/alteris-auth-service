"""
Push notifications to staff phones through Expo's push service, which sits in
front of Apple (APNs) and Google (FCM). Run as background tasks after the
request has succeeded, and never raise: a failed push is logged, not fatal.
https://docs.expo.dev/push-notifications/sending-notifications/
"""

import logging
import uuid
from collections.abc import Sequence

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.db.models.push_token import PushToken

log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
BATCH_SIZE = 100  # Expo's limit per request
# The app's Android channel for attendance notifications.
ATTENDANCE_CHANNEL = "attendance"


async def tokens_for(db: AsyncSession, user_ids: Sequence[uuid.UUID]) -> list[str]:
    if not user_ids:
        return []
    rows = await db.scalars(select(PushToken.token).where(PushToken.user_id.in_(user_ids)))
    return list(rows)


async def send_push(tokens: Sequence[str], *, title: str, body: str, data: dict[str, str]) -> None:
    if not tokens:
        return
    headers = {"Accept": "application/json"}
    access_token = get_settings().EXPO_ACCESS_TOKEN
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    unregistered: list[str] = []
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        for start in range(0, len(tokens), BATCH_SIZE):
            batch = tokens[start : start + BATCH_SIZE]
            messages = [
                {
                    "to": token,
                    "title": title,
                    "body": body,
                    "data": data,
                    "sound": "default",
                    "channelId": ATTENDANCE_CHANNEL,
                }
                for token in batch
            ]
            try:
                response = await client.post(EXPO_PUSH_URL, json=messages)
                response.raise_for_status()
                tickets = response.json().get("data", [])
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("could not send %d push notification(s): %s", len(batch), exc)
                continue
            # One ticket per message, in order.
            for token, ticket in zip(batch, tickets, strict=False):
                if ticket.get("status") == "ok":
                    continue
                error = (ticket.get("details") or {}).get("error")
                if error == "DeviceNotRegistered":
                    unregistered.append(token)
                else:
                    log.warning("push notification rejected (%s): %s", error, ticket.get("message"))

    if unregistered:
        # The app was uninstalled or the phone stopped accepting pushes.
        try:
            async with SessionLocal() as db:
                await db.execute(delete(PushToken).where(PushToken.token.in_(unregistered)))
                await db.commit()
        except Exception:
            log.exception("could not remove %d unregistered push token(s)", len(unregistered))
