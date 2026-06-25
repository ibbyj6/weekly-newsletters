"""
The Capital Chronicle — Weekly Private Credit Digest
=====================================================
Runs every Monday at 09:30 UK time via GitHub Actions.
Fetches private credit news from RSS feeds, summarises
with Google Gemini (free tier), sends formatted HTML
newsletter via Gmail SMTP.
"""

import os, sys, re, json, smtplib, logging, requests
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import google.generativeai as genai

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── CREDENTIALS ───────────────────────────────────────────────────────────────
GEMINI_API_KEY     = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
SEND_TO_EMAIL      = os.environ.get("SEND_TO_EMAIL", GMAIL_ADDRESS)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")   # free tier model

# ── RSS SOURCES ───────────────────────────────────────────────────────────────
RSS_SOURCES = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.ft.com/rss/home",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://api.axios.com/feed/",
    "https://seekingalpha.com/tag/private-credit.xml",
    "https://www.wsj.com/xml/rss/3_7031.xml",
]

KEYWORDS = [
    "private credit", "direct lending", "private debt",
    "leveraged loan", "CLO", "BDC", "business development",
    "middle market", "unitranche", "mezzanine", "credit fund",
    "Ares", "Blue Owl", "HPS", "Golub", "Owl Rock",
    "Blackstone credit", "Apollo credit", "KKR credit",
    "SOFR", "SONIA", "covenant", "payment-in-kind", "PIK",
    "default rate", "distressed", "NAV lending",
    "asset-based lending", "specialty finance", "direct lender",
]

# ── FETCH & FILTER ────────────────────────────────────────────────────────────
def fetch_rss(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CapitalChronicle/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        items = []
        for item in soup.find_all("item")[:30]:
            t = item.find("title")
            s = item.find("description") or item.find("summary")
            l = item.find("link")
            items.append({
                "title":   t.get_text(strip=True) if t else "",
                "summary": s.get_text(strip=True) if s else "",
                "link":    l.get_text(strip=True) if l else url,
            })
        return items
    except Exception as e:
        log.warning(f"RSS failed {url}: {e}")
        return []

def is_relevant(item):
    text = (item["title"] + " " + item["summary"]).lower()
    return any(k.lower() in text for k in KEYWORDS)

def gather_articles():
    log.info("Fetching RSS sources...")
    all_articles = []
    for url in RSS_SOURCES:
        arts = fetch_rss(url)
        rel  = [a for a in arts if is_relevant(a)]
        log.info(f"  {url.split('/')[2]}: {len(arts)} fetched, {len(rel)} relevant")
        all_articles.extend(rel)

    seen, unique = set(), []
    for a in all_articles:
        key = a["title"][:70].lower().strip()
        if key and key not in seen:
            seen.add(key); unique.append(a)

    log.info(f"Unique relevant articles: {len(unique)}")
    if not unique:
        return "No private credit news found this week.", []

    blob = []
    for i, a in enumerate(unique[:50], 1):
        blob.append(f"{i}. {a['title']}")
        if a["summary"]:
            clean = BeautifulSoup(a["summary"], "html.parser").get_text(strip=True)
            blob.append(f"   {clean[:250]}")
    return "\n".join(blob), unique[:50]

# ── GEMINI SUMMARISATION ──────────────────────────────────────────────────────
def generate_stories(raw_content):
    today    = date.today().strftime("%d %B %Y")
    week_num = date.today().isocalendar().week

    prompt = f"""You are a senior private credit analyst writing The Capital Chronicle, a weekly newsletter. Today is {today}, Week {week_num}.

From the source content below, identify the 5 most significant private credit stories of the week.

Return ONLY a valid JSON array with exactly 5 objects. No markdown, no preamble, no explanation.

Each object must have:
- "title": sharp headline max 12 words British English
- "category": one of: "Credit Quality", "Ratings Risk", "Spreads & Pricing", "BDC & Retail", "Regulatory", "Deal Flow", "Macro Watch"
- "category_color": pick the right pair — "Credit Quality"="#9b2335 bg=#fdf0ef", "Ratings Risk"="#7b3fa0 bg=#f5eefb", "Spreads & Pricing"="#0d6b3f bg=#eaf7f0", "BDC & Retail"="#b35c00 bg=#fff4e5", "Regulatory"="#1a5c8a bg=#e8f3fb", "Deal Flow"="#0d6b3f bg=#eaf7f0", "Macro Watch"="#5a5a00 bg=#fafadf"
- "tldr": array of exactly 3 strings, each starting with "·", specific and quantitative
- "body_p1": first analytical paragraph, 80-100 words, British English, no filler
- "body_p2": second analytical paragraph, 80-100 words, broadening context
- "stats": array of 4-5 short data point strings e.g. "Default rate: 6.0%"
- "implication": 3-4 sentence market implication paragraph for allocators and lenders

SOURCE CONTENT:
{raw_content}

Return only the JSON array."""

    log.info("Calling Gemini for story generation...")
    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        stories = json.loads(raw)
        log.info(f"Gemini returned {len(stories)} stories.")
        return stories
    except json.JSONDecodeError as e:
        log.error(f"JSON parse failed: {e}")
        return [{
            "title": "No structured stories generated this week",
            "category": "Macro Watch",
            "category_color": "#5a5a00 bg=#fafadf",
            "tldr": ["· Insufficient news volume this week", "· Check RSS feed sources", "· Manual review recommended"],
            "body_p1": "The automated news fetch did not return sufficient private credit content to generate structured stories this week.",
            "body_p2": "Please check the GitHub Actions log for details and consider adding additional RSS sources.",
            "stats": ["Articles fetched: 0", "Sources checked: 6"],
            "implication": "No market implication to report this week.",
        }]

# ── HTML BUILDER ──────────────────────────────────────────────────────────────
def parse_color(color_str):
    try:
        parts = color_str.split(" bg=")
        return parts[0].strip(), parts[1].strip()
    except:
        return "#1a5c8a", "#e8f3fb"

def summary_row(num, story):
    tc, bg = parse_color(story.get("category_color", "#1a5c8a bg=#e8f3fb"))
    lines  = story.get("tldr", [])
    blurb  = " ".join(l.lstrip("· ").strip() for l in lines[:2])[:160]
    return f"""
    <tr>
      <td width="28" style="vertical-align:top;padding:0 0 14px;">
        <div style="width:22px;height:22px;background:#0a1f35;border-radius:50%;text-align:center;line-height:22px;font-family:Arial,sans-serif;font-size:10px;font-weight:700;color:#c9a84c;">{num}</div>
      </td>
      <td style="vertical-align:top;padding:0 16px 14px 0;border-bottom:0.5px solid #edf1f6;">
        <p style="margin:0 0 2px;font-family:Arial,sans-serif;font-size:12px;font-weight:700;color:#0a1f35;">{story['title']}</p>
        <p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#5a7a96;line-height:1.55;">{blurb}</p>
      </td>
      <td width="80" style="vertical-align:top;padding:0 0 14px;text-align:right;border-bottom:0.5px solid #edf1f6;">
        <span style="font-family:Arial,sans-serif;font-size:9px;font-weight:700;color:{tc};background:{bg};padding:2px 8px;border-radius:10px;white-space:nowrap;">{story['category']}</span>
      </td>
    </tr>
    <tr><td colspan="3" style="padding:0 0 14px;"></td></tr>"""

def full_story(num, story, anchor):
    tc, bg = parse_color(story.get("category_color", "#1a5c8a bg=#e8f3fb"))
    tldr   = "\n".join(
        f'<p style="margin:0 0 5px;font-size:13px;color:#0a1f35;line-height:1.65;font-family:Arial,sans-serif;font-weight:600;">{l}</p>'
        for l in story.get("tldr", [])
    )
    stats  = "".join(
        f'<span style="display:inline-block;background:#eef3f9;border:0.5px solid #c8d9ea;padding:3px 10px;border-radius:12px;font-family:Arial,sans-serif;font-size:12px;color:#0a1f35;font-weight:600;margin:0 6px 4px 0;">{s}</span>'
        for s in story.get("stats", [])
    )
    return f"""
    <a name="{anchor}"></a>
    <p style="margin:0 0 5px;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#c9a84c;">Story {num:02d} &nbsp;/&nbsp; {story['category']}</p>
    <p style="margin:0 0 14px;font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#0a1f35;letter-spacing:0.03em;text-transform:uppercase;border-bottom:1.5px solid #e4eaf2;padding-bottom:8px;">{story['title']}</p>
    <div style="background:#f6f8fc;border-radius:4px;padding:14px 18px;margin:0 0 18px;">
      <span style="display:inline-block;background:#0a1f35;color:#c9a84c;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;padding:3px 10px;border-radius:2px;margin-bottom:10px;">TL;DR</span>
      {tldr}
    </div>
    <p style="font-size:14px;color:#2c2c2c;line-height:1.78;margin:0 0 12px;font-family:Georgia,serif;">{story.get('body_p1','')}</p>
    <p style="font-size:14px;color:#2c2c2c;line-height:1.78;margin:0 0 16px;font-family:Georgia,serif;">{story.get('body_p2','')}</p>
    <div style="margin:0 0 16px;">{stats}</div>
    <div style="background:#f6f8fc;border-left:3px solid #0a1f35;padding:12px 16px;margin:0 0 8px;border-radius:0 4px 4px 0;">
      <p style="font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#0a1f35;margin:0 0 5px;">Market Implication</p>
      <p style="font-family:Arial,sans-serif;font-size:13px;color:#0a1f35;line-height:1.65;margin:0;">{story.get('implication','')}</p>
    </div>
    <hr style="border:none;border-top:0.5px solid #dde5ee;margin:28px 0;">"""

def build_html(stories):
    today_str = date.today().strftime("%A, %d %B %Y")
    week_num  = date.today().isocalendar().week
    anchors   = ["s1","s2","s3","s4","s5"]

    nav = "".join(
        f'<td style="padding:9px {"0" if i==0 else "10"}px 9px 10px;"><a href="#{anchors[i]}" style="font-family:Arial,sans-serif;font-size:10px;color:#6b95b8;">{s["category"]}</a></td>'
        for i, s in enumerate(stories[:5])
    )
    rows   = "".join(summary_row(i+1, s) for i, s in enumerate(stories[:5]))
    blocks = "".join(full_story(i+1, s, anchors[i]) for i, s in enumerate(stories[:5]))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>The Capital Chronicle — Week {week_num}</title></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Georgia,serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:28px 0;"><tr><td align="center">
<table width="660" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.09);">

<tr><td style="background:#0a1f35;padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:28px 38px 18px;">
        <p style="margin:0 0 3px;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:#c9a84c;">Private Credit Intelligence</p>
        <h1 style="margin:0 0 5px;font-family:Georgia,serif;font-size:26px;font-weight:normal;color:#fff;letter-spacing:-0.02em;">The Capital Chronicle</h1>
        <p style="margin:0;font-family:Arial,sans-serif;font-size:12px;color:#6b95b8;">{today_str} &nbsp;·&nbsp; Week {week_num}</p>
      </td>
      <td style="padding:28px 38px 18px;text-align:right;vertical-align:middle;">
        <p style="margin:0;font-family:Arial,sans-serif;font-size:9px;color:#3d6280;text-transform:uppercase;letter-spacing:0.1em;">Issue</p>
        <p style="margin:3px 0 0;font-family:Arial,sans-serif;font-size:22px;font-weight:700;color:#c9a84c;">{week_num}</p>
      </td>
    </tr>
    <tr><td colspan="2" style="padding:0 38px;border-top:0.5px solid #1a3450;">
      <table cellpadding="0" cellspacing="0"><tr>{nav}</tr></table>
    </td></tr>
  </table>
</td></tr>

<tr><td style="padding:28px 38px 0;">
  <p style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#c9a84c;">This Week's Stories</p>
  <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
  <div style="border-top:2px solid #c9a84c;margin:8px 0 0;"></div>
</td></tr>

<tr><td style="padding:28px 38px 0;">{blocks}</td></tr>

<tr><td style="background:#0a1f35;padding:18px 38px;">
  <p style="margin:0;font-family:Arial,sans-serif;font-size:10px;color:#3d6280;line-height:1.7;">
    <strong style="color:#6b95b8;">The Capital Chronicle</strong> &nbsp;·&nbsp; Private Credit Intelligence &nbsp;·&nbsp; Week {week_num}, {date.today().strftime('%B %Y')}<br>
    Sources: Reuters · FT · Bloomberg · WSJ · Axios · Seeking Alpha<br>
    Auto-generated every Monday. For informational purposes only. Not investment advice.
  </p>
</td></tr>

</table></td></tr></table>
</body></html>"""

# ── SEND EMAIL ────────────────────────────────────────────────────────────────
def send_email(html_body, plain_body):
    week_num = date.today().isocalendar().week
    subject  = f"The Capital Chronicle — W{week_num} {date.today().strftime('%d %b %Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"The Capital Chronicle <{GMAIL_ADDRESS}>"
    msg["To"]      = SEND_TO_EMAIL
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))
    log.info(f"Sending to {SEND_TO_EMAIL}...")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls(); s.ehlo()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_ADDRESS, SEND_TO_EMAIL, msg.as_string())
    log.info("Capital Chronicle sent.")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info(f"The Capital Chronicle — {date.today()}")
    log.info("=" * 55)
    raw, articles = gather_articles()
    stories       = generate_stories(raw)
    html          = build_html(stories)
    plain         = f"The Capital Chronicle — Week {date.today().isocalendar().week}\n\n" + \
                    "\n".join(f"{i+1}. {s['title']}\n" + "\n".join(f"   {t}" for t in s.get("tldr",[])) for i, s in enumerate(stories))
    send_email(html, plain)
    log.info("Done.")

if __name__ == "__main__":
    main()
