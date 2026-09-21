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
- If something is uncertain, say "we do not know yet."
- If something failed, say "it did not work."
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
