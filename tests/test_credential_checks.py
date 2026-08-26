"""Catch damaged Podcast Index credentials at startup.

Every problem here otherwise surfaces as a 401 from Podcast Index the first time someone
searches -- three layers from the cause, and identical whether the value is wrong,
truncated, quoted, or the host clock is off.
"""

import httpx
import pytest
import respx

from podarium.clients.podcastindex import describe_credential_problems, verify_credentials

GOOD_KEY = "ABCD1234EFGH5678IJKL"
GOOD_SECRET = "s" * 40


def test_a_healthy_pair_reports_nothing():
    assert describe_credential_problems(GOOD_KEY, GOOD_SECRET) == []


def test_absent_credentials_are_not_a_problem():
    """Not configured is a supported state, not a misconfiguration."""
    assert describe_credential_problems(None, None) == []
    assert describe_credential_problems("", "") == []


def test_only_one_of_the_pair_is_flagged():
    assert "PODCASTINDEX_SECRET is empty" in " ".join(
        describe_credential_problems(GOOD_KEY, None)
    )
    assert "PODCASTINDEX_KEY is empty" in " ".join(
        describe_credential_problems(None, GOOD_SECRET)
    )


def test_a_truncated_secret_is_flagged():
    """The Compose interpolation case: a '$' in the value eats the rest of it."""
    problems = " ".join(describe_credential_problems(GOOD_KEY, "G"))
    assert "only 1 characters" in problems
    assert "$$" in problems, "should say how to escape it"


def test_an_uncollapsed_escape_is_flagged():
    """Escaped one time too many, so the doubling reached the container."""
    problems = " ".join(describe_credential_problems(GOOD_KEY, "G$$D5qg" + "s" * 33))
    assert "literal '$$'" in problems


def test_a_whole_pasted_line_is_flagged():
    """A KEY=value line dropped into a value field."""
    problems = " ".join(
        describe_credential_problems(f"PODCASTINDEX_KEY={GOOD_KEY}", GOOD_SECRET)
    )
    assert 'PODCASTINDEX_KEY=" on the front' in problems


def test_surrounding_quotes_are_flagged():
    problems = " ".join(describe_credential_problems(GOOD_KEY, f'"{GOOD_SECRET}"'))
    assert "surrounding quotes" in problems


def test_stray_whitespace_is_flagged():
    problems = " ".join(describe_credential_problems(GOOD_KEY, f"{GOOD_SECRET} "))
    assert "leading or trailing whitespace" in problems


@respx.mock
async def test_live_check_reports_acceptance(monkeypatch):
    from podarium.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "podcastindex_key", "k", raising=False)
    monkeypatch.setattr(settings, "podcastindex_secret", "s", raising=False)
    respx.get(url__startswith="https://api.podcastindex.org").mock(
        return_value=httpx.Response(200, json={"feeds": []})
    )

    assert await verify_credentials("test") == "accepted"


@respx.mock
async def test_live_check_explains_a_401(monkeypatch):
    """The message has to name the causes, because a 401 alone cannot distinguish them."""
    from podarium.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "podcastindex_key", "k", raising=False)
    monkeypatch.setattr(settings, "podcastindex_secret", "s", raising=False)
    respx.get(url__startswith="https://api.podcastindex.org").mock(
        return_value=httpx.Response(401)
    )

    result = await verify_credentials("test")
    assert "rejected (401)" in result
    assert "clock" in result


async def test_live_check_says_when_unconfigured():
    assert await verify_credentials("test") == "not configured"
