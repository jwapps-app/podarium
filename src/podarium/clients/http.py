import httpx

from podarium.config import get_settings


def build_client(user_agent: str, *, follow_redirects: bool = True) -> httpx.AsyncClient:
    """The only place outbound HTTP clients are constructed.

    Everything that leaves this process for a publisher host goes through here, which is
    what keeps the "one IP address, the server's" guarantee auditable.
    """
    settings = get_settings()
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=httpx.Timeout(settings.http_timeout_seconds, read=settings.http_timeout_seconds),
        follow_redirects=follow_redirects,
    )
