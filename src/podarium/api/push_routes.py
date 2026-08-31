"""Registering devices for new-episode notifications."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium import push
from podarium.auth import current_user
from podarium.db import get_session
from podarium.models import ApnsDevice, PushSubscription, User
from podarium.services import get_app_settings

router = APIRouter(prefix="/api/push", tags=["push"])


class PushConfigOut(BaseModel):
    # None means the server has no VAPID keys, so the client should not offer to subscribe.
    public_key: str | None
    subscribed: bool


class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    label: str | None = None


class PushDeviceOut(BaseModel):
    id: int
    label: str | None
    created_at: str


class ApnsDeviceRequest(BaseModel):
    device_token: str
    bundle_id: str
    # Debug builds run from Xcode get sandbox tokens, which APNs rejects on the production
    # host and the other way round. Only the device knows which it holds.
    sandbox: bool = False


class ApnsDeviceForget(BaseModel):
    device_token: str


@router.get("/config", response_model=PushConfigOut)
async def config(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PushConfigOut:
    count = len(
        (
            await session.execute(
                select(PushSubscription.id).where(PushSubscription.user_id == user.id)
            )
        ).scalars().all()
    )
    return PushConfigOut(public_key=push.public_key(), subscribed=count > 0)


@router.post("", response_model=PushDeviceOut, status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: PushSubscribeRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PushDeviceOut:
    """Register a browser's push endpoint.

    Upserts on the endpoint. A browser hands back the same endpoint when a page re-subscribes
    with the same key, so a user who enables notifications twice on one device should end up
    with one row rather than two and a doubled notification.
    """
    if not push.public_key():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server has no VAPID keys, so push is disabled",
        )

    # Every real push service is https, and this URL is one the server will POST to on a
    # schedule from inside the network. Accepting anything else would store a standing
    # instruction to deliver requests wherever the subscriber pointed.
    if not body.endpoint.startswith("https://"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Push endpoints must be https",
        )

    existing = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = PushSubscription(user_id=user.id, endpoint=body.endpoint)
        session.add(existing)

    existing.user_id = user.id
    existing.p256dh = body.p256dh
    existing.auth = body.auth
    existing.label = body.label
    await session.commit()
    await session.refresh(existing)

    return PushDeviceOut(
        id=existing.id, label=existing.label, created_at=existing.created_at.isoformat()
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    endpoint: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Drop one endpoint, or every device for this user when none is named."""
    statement = delete(PushSubscription).where(PushSubscription.user_id == user.id)
    if endpoint:
        statement = statement.where(PushSubscription.endpoint == endpoint)
    await session.execute(statement)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/device", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    body: ApnsDeviceRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Register an iPhone for notifications.

    Upserted on the token, because iOS reissues one on reinstall and occasionally
    otherwise -- the same phone arriving under a new token should replace its old row, and
    the same token arriving twice should not make two.
    """
    existing = (
        await session.execute(
            select(ApnsDevice).where(ApnsDevice.device_token == body.device_token)
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            ApnsDevice(
                user_id=user.id,
                device_token=body.device_token,
                bundle_id=body.bundle_id,
                sandbox=body.sandbox,
            )
        )
    else:
        existing.user_id = user.id
        existing.bundle_id = body.bundle_id
        existing.sandbox = body.sandbox

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/device", status_code=status.HTTP_204_NO_CONTENT)
async def forget_device(
    body: ApnsDeviceForget,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stop sending to a device -- signing out, or turning notifications off."""
    await session.execute(
        delete(ApnsDevice)
        .where(ApnsDevice.device_token == body.device_token)
        .where(ApnsDevice.user_id == user.id)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=PushConfigOut)
async def send_test(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PushConfigOut:
    """Send a notification to every registered device.

    Worth having because the failure modes here are all silent and all remote: permission
    granted to the wrong origin, a key that does not match the subscription, an endpoint the
    push service has quietly retired. One button that either buzzes your phone or does not
    is the whole diagnostic.
    """
    app_settings = await get_app_settings(session)
    await push.send_to_all(
        session,
        user.id,
        {"title": "Podarium", "body": "Notifications are working.", "url": "/"},
        user_agent=app_settings.user_agent,
    )
    return await config(user=user, session=session)
