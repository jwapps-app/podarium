from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podarium.auth import (
    clear_session_cookie,
    current_user,
    generate_api_token,
    issue_session_cookie,
    verify_password,
)
from podarium.config import Settings, get_settings
from podarium.db import get_session
from podarium.models import ApiToken, User
from podarium.throttle import record_attempt, seconds_until_unlocked
from podarium.schemas import (
    LoginRequest,
    TokenCreateRequest,
    TokenCreatedOut,
    TokenOut,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
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
    ok = user is not None and verify_password(user.password_hash, body.password)
    await record_attempt(session, body.username, succeeded=ok)

    if not ok:
        # Deliberately identical whether the username exists or the password is wrong.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    issue_session_cookie(response, user, settings)
    return UserOut(id=user.id, username=user.username, created_at=user.created_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: Settings = Depends(get_settings)) -> Response:
    clear_session_cookie(response, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username, created_at=user.created_at)


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
