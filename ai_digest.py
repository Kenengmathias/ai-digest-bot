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
LOOKBACK_HOURS = 72  # covers arXiv's weekend announcement gap; seen.json already blocks repeats
STATE_FILE = Path(__file__).parent / "seen.json"  # tracked + committed by the workflow
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
# Free-tier model IDs on OpenRouter rotate — check openrouter.ai/models?fmt=free if this stops working
# and override via the OPENROUTER_MODEL secret rather than editing this file.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL") or "meta-llama/llama-3.3-70b-instruct:free"


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
            "blurb": " ".join(entry.summary.split())[:600] if getattr(entry, "summary", None) else "",
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
            items.append({
                "id": entry.link,
                "title": entry.title,
                "link": entry.link,
                "source": source,
                "blurb": " ".join(entry.summary.split())[:400] if getattr(entry, "summary", None) else "",
            })
    return items


def summarize_items(items):
    """One batched OpenRouter call for all items (not one call per item — keeps free-tier
    usage to ~2 requests/day regardless of how many papers/articles came in). Returns a
    list of plain-English one-liners aligned to `items`; empty strings on any failure so
    a bad/missing key never breaks the digest."""
    if not OPENROUTER_API_KEY or not items:
        return ["" for _ in items]

    numbered = "\n".join(
        f"{i+1}. {it['title']}" + (f" — {it['blurb']}" if it.get("blurb") else "")
        for i, it in enumerate(items)
    )
    prompt = (
        "For each numbered AI/tech item below, write ONE short, plain-English sentence "
        "(under 25 words) explaining what it is and why a non-technical reader might care. "
        "Reply with ONLY a JSON array of strings, same order as the input, nothing else "
        "(no markdown fences, no commentary).\n\n" + numbered
    )
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"OpenRouter error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        content = content.strip("`").removeprefix("json").strip()
        summaries = json.loads(content)
        if not isinstance(summaries, list) or len(summaries) != len(items):
            raise ValueError(f"expected {len(items)} summaries, got {summaries!r}")
        return [str(s) for s in summaries]
    except Exception as e:
        print(f"Summarization skipped ({e})")
        return ["" for _ in items]


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — printing digest instead:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):  # Telegram's 4096-char message cap
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[i:i + 4000],
            "disable_web_page_preview": True,
        }, timeout=20)
        if resp.status_code != 200:
            print(f"Telegram send failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()  # stop here on failure — don't let main() mark items as seen


def build_digest(papers, articles, seen):
    new_papers = [p for p in papers if p["id"] not in seen]
    new_articles = [a for a in articles if a["id"] not in seen]
    if not new_papers and not new_articles:
        return None, seen

    all_new = new_papers + new_articles
    summaries = summarize_items(all_new)
    for item, summary in zip(all_new, summaries):
        item["ai_summary"] = summary

    lines = [f"AI/Tech Digest — {datetime.now().strftime('%b %d, %Y')}\n"]
    if new_papers:
        lines.append("── arXiv Papers ──")
        for p in new_papers:
            entry = f"• {p['title']}"
            if p["ai_summary"]:
                entry += f"\n  {p['ai_summary']}"
            entry += f"\n  {p['link']}"
            lines.append(entry)
    if new_articles:
        lines.append("\n── Tech News ──")
        for a in new_articles:
            entry = f"• [{a['source']}] {a['title']}"
            if a["ai_summary"]:
                entry += f"\n  {a['ai_summary']}"
            entry += f"\n  {a['link']}"
            lines.append(entry)

    for item in all_new:
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
