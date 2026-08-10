#!/usr/bin/env python3
"""
AI/Tech Digest — fetches recent arXiv papers + tech news RSS, sends via Telegram.
Runs on a schedule via GitHub Actions (see .github/workflows/digest.yml).
"""

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import feedparser

# ---- Config ----
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]
ARXIV_MAX_RESULTS = 15
RSS_FEEDS = {
    "MIT Tech Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
}
LOOKBACK_HOURS = 24
STATE_FILE = Path(__file__).parent / "seen.json"  # tracked + committed by the workflow
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(list(seen)[-500:]))  # cap growth


def fetch_arxiv():
    cat_query = "+OR+".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    url = (
        "http://export.arxiv.org/api/query?"
        f"search_query={cat_query}&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={ARXIV_MAX_RESULTS}"
    )
    resp = requests.get(url, timeout=20)
    feed = feedparser.parse(resp.text)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    papers = []
    for entry in feed.entries:
        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if published < cutoff:
            continue
        papers.append({
            "id": entry.id,
            "title": " ".join(entry.title.split()),
            "link": entry.link,
        })
    return papers


def fetch_rss():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    items = []
    for source, url in RSS_FEEDS.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            if getattr(entry, "published_parsed", None):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if published < cutoff:
                    continue
            items.append({"id": entry.link, "title": entry.title, "link": entry.link, "source": source})
    return items


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — printing digest instead:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):  # Telegram's 4096-char message cap
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[i:i + 4000],
            "disable_web_page_preview": True,
        }, timeout=20)


def build_digest(papers, articles, seen):
    new_papers = [p for p in papers if p["id"] not in seen]
    new_articles = [a for a in articles if a["id"] not in seen]
    if not new_papers and not new_articles:
        return None, seen

    lines = [f"AI/Tech Digest — {datetime.now().strftime('%b %d, %Y')}\n"]
    if new_papers:
        lines.append("── arXiv Papers ──")
        for p in new_papers:
            lines.append(f"• {p['title']}\n  {p['link']}")
    if new_articles:
        lines.append("\n── Tech News ──")
        for a in new_articles:
            lines.append(f"• [{a['source']}] {a['title']}\n  {a['link']}")

    for item in new_papers + new_articles:
        seen.add(item["id"])
    return "\n".join(lines), seen


def main():
    seen = load_seen()
    papers = fetch_arxiv()
    articles = fetch_rss()
    digest, seen = build_digest(papers, articles, seen)
    if digest:
        send_telegram(digest)
        save_seen(seen)
    else:
        print("No new items.")


if __name__ == "__main__":
    main()

