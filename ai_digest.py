#!/usr/bin/env python3
"""
AI Paper Digest — picks 1 unseen arXiv paper per run, deep-analyzes it, sends via Telegram.
Runs twice daily via GitHub Actions cron (morning + evening = 2 papers per day).
"""

import os
import json
import requests
import feedparser
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pypdf import PdfReader
from openai import OpenAI

# ---- Config ----
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]
ARXIV_MAX_RESULTS = 20
LOOKBACK_HOURS = 72
STATE_FILE = Path(__file__).parent / "seen.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL   = "openrouter/free"

ANALYSIS_PROMPT = """
You are explaining an AI research paper to a smart, self-taught
entrepreneur who builds software products. He is not an academic.
He does not know research jargon.

Your job is to extract what matters and explain it in plain English.
Write like you are texting a smart friend, not writing a report.

No jargon. No academic language. No buzzwords.
If you must use a technical term, explain it in brackets immediately.

Be short. One idea per sentence. Skip anything that does not matter
to someone who wants to build something real.

Return EXACTLY these 5 sections:

------------------------------------------------------------
1. WHAT IS THIS ACTUALLY ABOUT?
------------------------------------------------------------

Explain the core idea in 2-3 sentences a 16-year-old could follow.
What problem does it solve? What did they try?

------------------------------------------------------------
2. WHAT ACTUALLY WORKED?
------------------------------------------------------------

What result did they get that was genuinely impressive?
Give the number, but explain what the number means in plain English.
Example: "The robot avoided hitting objects 87% of the time,
compared to 59% without this method — that is a big jump."

------------------------------------------------------------
3. WHERE IT BREAKS IN THE REAL WORLD
------------------------------------------------------------

Forget the lab. What would actually go wrong if you tried to
deploy this today?
What conditions does it need that the real world will not give it?
What would need to be true before you could trust this outside
a controlled setting?

------------------------------------------------------------
4. WHAT DOES THIS MAKE POSSIBLE?
------------------------------------------------------------

What can someone BUILD because of this research?

PROVEN: What does the experiment actually show you can do?
LIKELY: What could probably be built based on this?
SPECULATIVE: What might become possible in the future if this holds up?

------------------------------------------------------------
5. WHAT WOULD YOU ACTUALLY NEED TO BUILD THIS?
------------------------------------------------------------

Pretend someone wants to turn this into a real product today.
List only the real obstacles — skip the obvious stuff.
What would actually stop you or slow you down?

------------------------------------------------------------
BOTTOM LINE
------------------------------------------------------------

3-4 sentences only.
What is genuinely interesting here?
What is the most useful thing this unlocks?
What is the biggest unsolved problem before it becomes real?

Do not hype it. Do not dismiss it. Just tell me what changed.

At the very end, on its own line, sign exactly like this:
— [your actual model name]

------------------------------------------------------------
RULES
------------------------------------------------------------

- Write like a smart friend explaining over WhatsApp.
- No academic tone. No passive voice. No long sentences.
- If something is uncertain, say "we do not know yet" not
  "further research is warranted."
- If something failed, say "it did not work" not
  "performance degraded under distribution shift."
- Always explain what a result MEANS, not just what it is.

Here is the research paper:

================ PAPER START ================

"""


# ---- State ----

def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()

def save_seen(seen):
    STATE_FILE.write_text(json.dumps(list(seen)[-500:]))


# ---- Fetch ----

def fetch_arxiv_papers():
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
        arxiv_id = entry.id.split("/abs/")[-1]
        papers.append({
            "id":       entry.id,
            "arxiv_id": arxiv_id,
            "title":    " ".join(entry.title.split()),
            "link":     entry.link,
        })
    return papers


# ---- PDF ----

def download_pdf(arxiv_id):
    url  = f"https://arxiv.org/pdf/{arxiv_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return BytesIO(resp.content)

def extract_text(pdf_file):
    reader = PdfReader(pdf_file)
    pages  = [page.extract_text() for page in reader.pages if page.extract_text()]
    return "\n\n".join(pages)


# ---- Analyze ----

def analyze_paper(paper_text):
    if not OPENROUTER_API_KEY:
        return "OpenRouter API key not configured."

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    prompt = (
        ANALYSIS_PROMPT
        + paper_text[:40000]
        + "\n\n================= PAPER END =================\n"
    )

    try:
        response = client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Analysis failed: {e}"


# ---- Telegram ----

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured — printing instead:\n")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        resp = requests.post(url, data={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     text[i:i + 4000],
            "disable_web_page_preview": True,
        }, timeout=20)
        resp.raise_for_status()


# ---- Main ----

def main():
    seen   = load_seen()
    papers = fetch_arxiv_papers()

    target = next((p for p in papers if p["id"] not in seen), None)
    if not target:
        print("No new papers.")
        return

    print(f"Analyzing: {target['title']}")

    try:
        pdf_file   = download_pdf(target["arxiv_id"])
        paper_text = extract_text(pdf_file)
    except Exception as e:
        print(f"Failed to fetch paper: {e}")
        return

    if not paper_text.strip():
        print("Could not extract text.")
        return

    analysis = analyze_paper(paper_text)
    message  = f"📄 {target['title']}\n{target['link']}\n\n{analysis}"

    send_telegram(message)

    seen.add(target["id"])
    save_seen(seen)


if __name__ == "__main__":
    main()
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
    """One batched OpenRouter call for all items. Returns a list of plain-English 
    one-liners aligned to `items`; empty strings on any failure so a bad/missing 
    key never breaks the digest."""
    if not OPENROUTER_API_KEY or not items:
        return ["" for _ in items]

    numbered = "\n".join(
        f"{i+1}. {it['title']}" + (f" — {it['blurb']}" if it.get("blurb") else "")
        for i, it in enumerate(items)
    )
    prompt = (
        "For each numbered AI/tech item below, write ONE short, plain-English sentence "
        "(under 25 words) explaining what it is and why a non-technical reader might care. "
        "Reply with ONLY a valid raw JSON array of strings, same order as the input, nothing else. "
        "Do NOT wrap the output in markdown code blocks or backticks.\n\n" + numbered
    )
    try:
        # Using OpenRouter's plural 'models' feature so it auto-swaps if one fails!
        payload = {
            "models": [
                OPENROUTER_MODEL, 
                "openai/gpt-oss-20b:free"
            ],
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}  # Forces JSON generation
        }

        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"OpenRouter error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
        
        content = resp.json()["choices"][0]["message"]["content"].strip()
        
        # Robust Markdown stripping logic
        if content.startswith("```"):
            # Split lines, remove the first line (```json) and last line (```)
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        summaries = json.loads(content)
        
        # If the model wrapped the array in a parent object dictionary
        if isinstance(summaries, dict):
            for key in summaries.keys():
                if isinstance(summaries[key], list):
                    summaries = summaries[key]
                    break

        if not isinstance(summaries, list) or len(summaries) != len(items):
            raise ValueError(f"expected {len(items)} summaries, got {summaries!r}")
        return [str(s) for s in summaries]
        
    except Exception as e:
        print(f"Summarization skipped due to error: ({e})")
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
