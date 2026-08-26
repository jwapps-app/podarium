"""Feed XML builders for tests."""

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{title}</title>
    <link>https://example.com/show</link>
    <description>A show</description>
    <language>en-us</language>
    {items}
  </channel>
</rss>
"""

ITEM = """
    <item>
      <title>{title}</title>
      <guid isPermaLink="false">{guid}</guid>
      <pubDate>{pub_date}</pubDate>
      <itunes:duration>{duration}</itunes:duration>
      <enclosure url="{url}" type="audio/mpeg" length="{length}" />
    </item>
"""


def build_feed(*, title="Test Show", items):
    """items: list of dicts with guid, title, pub_date, and optional url/length/duration."""
    rendered = "".join(
        ITEM.format(
            title=item["title"],
            guid=item["guid"],
            pub_date=item["pub_date"],
            duration=item.get("duration", "00:30:00"),
            url=item.get("url", f"https://cdn.example.com/{item['guid']}.mp3"),
            length=item.get("length", 1000),
        )
        for item in items
    )
    return TEMPLATE.format(title=title, items=rendered).encode()
