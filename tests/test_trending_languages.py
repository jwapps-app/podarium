"""Trending is filtered to what the device reads.

Unfiltered, the index's trending list is global, and globally most podcasts are not in
your language -- a sample came back a third Russian, German, French and Swedish. The
client sends its own language list; this is the part that decides what is safe to forward.
"""

from podarium.api.search_routes import MAX_LANGUAGES, parse_languages


class TestParseLanguages:
    def test_a_regional_tag_brings_its_base_along(self):
        # The index matches tags exactly, and publishers declare "en" or "en-US" at whim.
        # Asking for en-US alone drops every plain-"en" feed, which is most of them.
        assert parse_languages("en-US") == ["en-US", "en"]

    def test_a_browsers_ordered_list_keeps_its_order_without_repeating(self):
        assert parse_languages("en-US,en") == ["en-US", "en"]

    def test_a_second_language_is_kept(self):
        assert parse_languages("en-GB,es-MX") == ["en-GB", "en", "es-MX", "es"]

    def test_nothing_asked_for_means_no_filter(self):
        # Which leaves trending global -- the behaviour before any of this, and a
        # reasonable floor.
        assert parse_languages(None) == []
        assert parse_languages("") == []

    def test_junk_is_dropped_rather_than_forwarded(self):
        # This ends up in a request to another service, so it is matched against a shape.
        assert parse_languages("../../etc/passwd") == []
        assert parse_languages("en; DROP TABLE feeds") == []
        assert parse_languages("<script>") == []
        assert parse_languages("e") == []
        assert parse_languages("englishlanguage") == []

    def test_one_bad_tag_does_not_lose_the_good_ones(self):
        # A device offering something odd among several should still get the rest.
        assert parse_languages("!!,en-US,??") == ["en-US", "en"]

    def test_the_list_is_capped(self):
        many = ",".join(f"a{n}-XX" for n in range(20))
        assert len(parse_languages(many)) <= MAX_LANGUAGES

    def test_case_is_left_alone_but_not_duplicated(self):
        # The index matches case-insensitively, so "en" after "EN" would be noise.
        assert parse_languages("EN,en") == ["EN"]
