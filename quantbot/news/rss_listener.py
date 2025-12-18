from __future__ import annotations

import time
import feedparser
from quantbot.common.types import NewsItem
from quantbot.news.keyword import KeywordScorer
from quantbot.utils.time import utc_now

class RSSNewsListener:
    def __init__(self, feeds: list[str], scorer: KeywordScorer, poll_sec: int = 30):
        self.feeds = feeds
        self.scorer = scorer
        self.poll_sec = poll_sec
        self._seen = set()

    def poll_once(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        for url in self.feeds:
            d = feedparser.parse(url)
            for e in d.entries:
                uid = getattr(e, "id", None) or getattr(e, "link", None) or getattr(e, "title", "")
                if not uid or uid in self._seen:
                    continue
                self._seen.add(uid)
                title = getattr(e, "title", "")
                summary = getattr(e, "summary", "") if hasattr(e, "summary") else ""
                text = f"{title} {summary}"
                s, hits = self.scorer.score(text)
                items.append(NewsItem(
                    ts=utc_now(), source="rss", title=title[:500], body=summary[:3500],
                    url=getattr(e, "link", ""), score=s, hits=hits
                ))
        return items

    def loop(self):
        while True:
            for it in self.poll_once():
                yield it
            time.sleep(self.poll_sec)
