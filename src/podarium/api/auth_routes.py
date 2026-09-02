import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import (
    clear_session_cookie,
    current_user,
    generate_api_token,
    hash_password,
    issue_session_cookie,
    request_is_secure,
    verify_password,
)
from podarium.config import Settings, get_settings
from podarium.db import get_session
from podarium.models import ApiToken, User
from podarium.throttle import record_attempt, seconds_until_unlocked
from podarium.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    provisioning_uri,
)
from podarium.totp import verify as verify_totp
from podarium.schemas import (
    LoginRequest,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpSetupOut,
    TokenCreateRequest,
    TokenCreatedOut,
    TokenOut,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Hashed once at import, from a value nobody knows. See login().
_UNKNOWN_USER_HASH = hash_password(secrets.token_hex(32))


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        created_at=user.created_at,
        totp_enabled=user.totp_secret is not None,
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    wait = await seconds_until_unlocked(session, body.username)
    if wait:
        # 429 rather than 401, so a client can tell "too many tries" from "wrong password"
        # and stop retrying. Checked before the password so a locked account cannot be
        # probed by timing how long the answer takes.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed sign-ins. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )

    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None:
        # Verified against a throwaway hash so an unknown name takes as long as a wrong
        # password. Skipping the work here made the two distinguishable by the clock,
        # whatever the response body said.
        verify_password(_UNKNOWN_USER_HASH, body.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, body.password)

    if not ok:
        await record_attempt(session, body.username, succeeded=False)
        # Identical in body and in timing whether the username exists or the password is
        # wrong.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.totp_secret:
        secret = decrypt_secret(user.totp_secret, settings.secret_key)
        if secret is None:
            # SECRET_KEY has changed since the secret was stored, so it can no longer be
            # read. Failing closed with a distinct message, rather than pretending the code
            # was wrong, is the difference between a fixable problem and a mystery.
            await record_attempt(session, body.username, succeeded=False)
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "The stored two-factor secret cannot be read, which happens when "
                    "SECRET_KEY changes. Clear it on the host to sign in again."
                ),
            )

        if not body.totp_code:
            # A wrong password and a missing code both leave you signed out, but only one
            # of them means "now show me the code field".
            await record_attempt(session, body.username, succeeded=False)
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="totp_required"
            )

        step = verify_totp(secret, body.totp_code, last_step=user.totp_last_step)
        if step is None:
            await record_attempt(session, body.username, succeeded=False)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        # Remember the step, so this code cannot be used again inside its window.
        user.totp_last_step = step

    await record_attempt(session, body.username, succeeded=True)
    issue_session_cookie(response, user, settings, secure=request_is_secure(request))
    return _user_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: Settings = Depends(get_settings)) -> Response:
    clear_session_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


@router.post("/totp/setup", response_model=TotpSetupOut)
async def totp_setup(
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> TotpSetupOut:
    """Mint a secret to scan. Nothing changes until a code from it is confirmed.

    Deliberately not stored yet: enabling on a secret that was never successfully scanned
    would lock the account out on the next sign-in.
    """
    if user.totp_secret is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Two-factor authentication is already on. Turn it off first.",
        )
    secret = generate_secret()
    return TotpSetupOut(
        secret=secret, provisioning_uri=provisioning_uri(secret, user.username)
    )


@router.post("/totp/enable", response_model=UserOut)
async def totp_enable(
    body: TotpEnableRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    """Confirm a code from the pending secret, then store it.

    The secret arrives in the body. It used to be a query parameter, which put it in the
    access log of every proxy and container between the browser and here.
    """
    if user.totp_secret is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Two-factor authentication is already on."
        )

    secret = body.secret
    step = verify_totp(secret, body.code)
    if step is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="That code did not match. Check the clock on your phone and try again.",
        )

    user.totp_secret = encrypt_secret(secret, settings.secret_key)

    # Deliberately not recording this step. Doing so would refuse the very code still on
    # screen if you signed out and straight back in, and it would say "invalid
    # credentials" while doing it. Replaying the setup code buys nothing anyway: reaching
    # this endpoint already required both the password and a live session.
    user.totp_last_step = None

    await session.commit()
    return _user_out(user)


@router.post("/totp/disable", response_model=UserOut)
async def totp_disable(
    body: TotpDisableRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Turn it off. Requires the password again, not just a live session."""
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")

    user.totp_secret = None
    user.totp_last_step = None
    await session.commit()
    return _user_out(user)


@router.get("/token", response_model=list[TokenOut])
async def list_tokens(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[TokenOut]:
    tokens = (
        await session.execute(
            select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id)
        )
    ).scalars().all()
    return [
        TokenOut(id=t.id, name=t.name, created_at=t.created_at, last_used_at=t.last_used_at)
        for t in tokens
    ]


@router.post("/token", response_model=TokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: TokenCreateRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TokenCreatedOut:
    plaintext, token_hash = generate_api_token()
    token = ApiToken(user_id=user.id, name=body.name, token_hash=token_hash)
    session.add(token)
    await session.commit()
    await session.refresh(token)
    # The plaintext is returned here and nowhere else; only its hash is stored.
    return TokenCreatedOut(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        token=plaintext,
    )


@router.delete("/token/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    token = await session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Token not found")
    await session.delete(token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
