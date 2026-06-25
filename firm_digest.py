"""
The Firm Intelligence Digest — Weekly Newsletter
=================================================
Runs every Monday at 09:30 UK time via GitHub Actions.
Searches for latest news on specific firms across five themes,
summarises with Google Gemini (free tier), sends formatted
HTML newsletter via Gmail SMTP.
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
model = genai.GenerativeModel("gemini-1.5-flash")

# ── FIRM-SPECIFIC SEARCH TARGETS ──────────────────────────────────────────────
# Each theme has RSS sources AND specific keyword filters for your firms
THEMES = {
    "private_credit": {
        "label": "Private Credit",
        "color": "#9b2335 bg=#fdf0ef",
        "firms": ["Partners Group", "Third Point", "Apollo", "Rohatyn Group",
                  "TPG", "General Atlantic", "Actis", "Tikehau Capital"],
        "keywords": ["private credit", "direct lending", "Partners Group",
                     "Third Point credit", "Apollo credit", "Rohatyn",
                     "TPG credit", "General Atlantic credit", "Actis",
                     "Tikehau Capital", "BDC", "leveraged loan", "unitranche"],
    },
    "islamic_finance": {
        "label": "Islamic Finance",
        "color": "#0d6b4f bg=#eaf7f0",
        "firms": ["AGL", "Aditum", "Third Point", "Tikehau Capital", "Franklin Templeton"],
        "keywords": ["Islamic finance", "Sukuk", "Shariah", "halal fund",
                     "AGL Capital", "Aditum", "Third Point Islamic",
                     "Tikehau Islamic", "Franklin Templeton Shariah",
                     "Islamic fund", "Murabaha", "Ijara", "AAOIFI"],
    },
    "capital_raising": {
        "label": "Capital Raising",
        "color": "#7b3fa0 bg=#f5eefb",
        "firms": ["Tikehau Capital", "AGL"],
        "keywords": ["Tikehau Capital fundraise", "Tikehau fund close",
                     "AGL Capital raise", "AGL fund", "private fund close",
                     "capital raise", "fund launch", "fund close", "first close",
                     "final close", "GP stake", "Tikehau AUM"],
    },
    "balance_sheet": {
        "label": "Balance Sheet Optimisation",
        "color": "#b35c00 bg=#fff4e5",
        "firms": ["Man Group", "Blackstone"],
        "keywords": ["significant risk transfer", "SRT", "Man Group",
                     "Blackstone credit", "balance sheet optimisation",
                     "synthetic securitisation", "RWA relief", "Basel IV",
                     "capital relief trade", "CLO", "structured credit",
                     "Man GLG", "Blackstone SRT"],
    },
    "digital_assets": {
        "label": "Digital Assets",
        "color": "#1a5c8a bg=#e8f3fb",
        "firms": ["Apollo", "Franklin Templeton", "LSEG", "Intain", "DropRWA"],
        "keywords": ["tokenisation", "RWA", "real world assets", "digital assets",
                     "Apollo tokenise", "Franklin Templeton digital",
                     "LSEG digital", "Intain", "DropRWA", "blockchain finance",
                     "on-chain", "DeFi institutional", "digital securities",
                     "tokenised fund", "BENJI"],
    },
}

RSS_SOURCES = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.ft.com/rss/home",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://api.axios.com/feed/",
    "https://www.wsj.com/xml/rss/3_7031.xml",
    "https://seekingalpha.com/tag/private-credit.xml",
    "https://cointelegraph.com/rss",                         # digital assets
    "https://www.coindesk.com/arc/outboundfeeds/rss/",       # digital assets
    "https://islamicfinancenews.com/feed",                    # Islamic finance
]

# ── FETCH & FILTER ────────────────────────────────────────────────────────────
def fetch_rss(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FirmDigest/1.0)"}
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

def gather_by_theme():
    """Fetch all RSS, then bucket articles into themes by keyword match."""
    log.info("Fetching all RSS sources...")
    all_articles = []
    for url in RSS_SOURCES:
        arts = fetch_rss(url)
        log.info(f"  {url.split('/')[2]}: {len(arts)} articles")
        all_articles.extend(arts)

    # Deduplicate
    seen, unique = set(), []
    for a in all_articles:
        key = a["title"][:70].lower().strip()
        if key and key not in seen:
            seen.add(key); unique.append(a)

    log.info(f"Total unique articles: {len(unique)}")

    # Bucket into themes
    bucketed = {theme: [] for theme in THEMES}
    for a in unique:
        text = (a["title"] + " " + a["summary"]).lower()
        for theme_key, theme in THEMES.items():
            if any(kw.lower() in text for kw in theme["keywords"]):
                bucketed[theme_key].append(a)

    for k, v in bucketed.items():
        log.info(f"  Theme '{k}': {len(v)} articles matched")

    return bucketed

def theme_blob(articles):
    if not articles:
        return "No specific articles found this week for this theme."
    lines = []
    for i, a in enumerate(articles[:15], 1):
        lines.append(f"{i}. {a['title']}")
        if a["summary"]:
            clean = BeautifulSoup(a["summary"], "html.parser").get_text(strip=True)
            lines.append(f"   {clean[:220]}")
    return "\n".join(lines)

# ── GEMINI: GENERATE ONE STORY PER THEME ─────────────────────────────────────
def generate_story(theme_key, theme_data, articles):
    blob     = theme_blob(articles)
    today    = date.today().strftime("%d %B %Y")
    week_num = date.today().isocalendar().week
    firms    = ", ".join(theme_data["firms"])

    prompt = f"""You are a senior finance analyst writing The Firm Intelligence Digest, a weekly newsletter. Today is {today}, Week {week_num}.

Theme: {theme_data['label']}
Key firms to cover where possible: {firms}

From the source content below, write ONE compelling story for this theme. Where possible, reference specific firms from the list above.

Return ONLY a valid JSON object with these exact keys. No markdown, no preamble.

{{
  "title": "sharp specific headline max 12 words British English",
  "firms_mentioned": ["list", "of", "firms", "actually", "referenced"],
  "tldr": ["· bullet 1 quantitative", "· bullet 2 quantitative", "· bullet 3 quantitative"],
  "body_p1": "first analytical paragraph 80-100 words British English no filler",
  "body_p2": "second analytical paragraph 80-100 words broadening context",
  "stats": ["Data point 1", "Data point 2", "Data point 3", "Data point 4"],
  "implication": "3-4 sentence market implication paragraph"
}}

SOURCE CONTENT:
{blob}

Return only the JSON object."""

    log.info(f"Calling Gemini for theme: {theme_data['label']}...")
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        story = json.loads(raw)
        story["category"]       = theme_data["label"]
        story["category_color"] = theme_data["color"]
        return story
    except Exception as e:
        log.error(f"Gemini failed for {theme_key}: {e}")
        return {
            "title": f"No {theme_data['label']} stories this week",
            "category": theme_data["label"],
            "category_color": theme_data["color"],
            "firms_mentioned": [],
            "tldr": ["· Insufficient data this week", "· Check sources", "· Manual review recommended"],
            "body_p1": f"Insufficient news content was found for {theme_data['label']} this week.",
            "body_p2": "Please check the GitHub Actions log for details.",
            "stats": ["Articles found: 0"],
            "implication": "No market implication to report this week.",
        }

def generate_all_stories(bucketed):
    stories = []
    for theme_key, theme_data in THEMES.items():
        story = generate_story(theme_key, theme_data, bucketed[theme_key])
        stories.append(story)
    return stories

# ── HTML BUILDER ──────────────────────────────────────────────────────────────
def parse_color(color_str):
    try:
        parts = color_str.split(" bg=")
        return parts[0].strip(), parts[1].strip()
    except:
        return "#1a5c8a", "#e8f3fb"

def firm_tags(story):
    firms = story.get("firms_mentioned", [])
    if not firms:
        return ""
    tags = "".join(
        f'<span style="display:inline-block;background:#0a1f35;color:#c9a84c;font-family:Arial,sans-serif;font-size:9px;font-weight:600;padding:2px 8px;border-radius:10px;margin:0 4px 0 0;">{f}</span>'
        for f in firms[:4]
    )
    return f'<div style="margin:0 0 12px;">{tags}</div>'

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
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 6px;">
      <tr>
        <td><p style="margin:0;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#c9a84c;">Story {num:02d} &nbsp;/&nbsp; {story['category']}</p></td>
        <td style="text-align:right;"><span style="font-family:Arial,sans-serif;font-size:9px;color:#9b9b9b;">{' · '.join(story.get('firms_mentioned', [])[:3])}</span></td>
      </tr>
    </table>
    <p style="margin:0 0 14px;font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#0a1f35;letter-spacing:0.03em;text-transform:uppercase;border-bottom:1.5px solid #e4eaf2;padding-bottom:8px;">{story['title']}</p>
    {firm_tags(story)}
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
    anchors   = ["pc","if","cr","bs","da"]

    nav = "".join(
        f'<td style="padding:9px {"0" if i==0 else "10"}px 9px 10px;"><a href="#{anchors[i]}" style="font-family:Arial,sans-serif;font-size:10px;color:#6b95b8;">{s["category"]}</a></td>'
        for i, s in enumerate(stories[:5])
    )
    rows   = "".join(summary_row(i+1, s) for i, s in enumerate(stories[:5]))
    blocks = "".join(full_story(i+1, s, anchors[i]) for i, s in enumerate(stories[:5]))

    all_firms = sorted(set(
        f for s in stories for f in s.get("firms_mentioned", [])
    ))
    firms_str = " · ".join(all_firms) if all_firms else "Various"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>The Firm Intelligence Digest — Week {week_num}</title></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Georgia,serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:28px 0;"><tr><td align="center">
<table width="660" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.09);">

<tr><td style="background:#0a1f35;padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:28px 38px 18px;">
        <p style="margin:0 0 3px;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:#c9a84c;">Intelligence Digest</p>
        <h1 style="margin:0 0 5px;font-family:Georgia,serif;font-size:26px;font-weight:normal;color:#fff;letter-spacing:-0.02em;">The Firm Intelligence Digest</h1>
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
  <p style="margin:0 0 16px;font-family:Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#c9a84c;">In This Issue</p>
  <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
  <div style="border-top:2px solid #c9a84c;margin:8px 0 0;"></div>
</td></tr>

<tr><td style="padding:28px 38px 0;">{blocks}</td></tr>

<tr><td style="background:#0a1f35;padding:18px 38px;">
  <p style="margin:0;font-family:Arial,sans-serif;font-size:10px;color:#3d6280;line-height:1.7;">
    <strong style="color:#6b95b8;">The Firm Intelligence Digest</strong> &nbsp;·&nbsp; Week {week_num}, {date.today().strftime('%B %Y')}<br>
    Firms: {firms_str}<br>
    Themes: Private Credit · Islamic Finance · Capital Raising · Balance Sheet · Digital Assets<br>
    Auto-generated every Monday. For informational purposes only. Not investment advice.
  </p>
</td></tr>

</table></td></tr></table>
</body></html>"""

# ── SEND EMAIL ────────────────────────────────────────────────────────────────
def send_email(html_body, plain_body):
    week_num = date.today().isocalendar().week
    subject  = f"The Firm Intelligence Digest — W{week_num} {date.today().strftime('%d %b %Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"The Firm Intelligence Digest <{GMAIL_ADDRESS}>"
    msg["To"]      = SEND_TO_EMAIL
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))
    log.info(f"Sending to {SEND_TO_EMAIL}...")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls(); s.ehlo()
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_ADDRESS, SEND_TO_EMAIL, msg.as_string())
    log.info("Firm Intelligence Digest sent.")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info(f"The Firm Intelligence Digest — {date.today()}")
    log.info("=" * 55)
    bucketed = gather_by_theme()
    stories  = generate_all_stories(bucketed)
    html     = build_html(stories)
    plain    = f"The Firm Intelligence Digest — Week {date.today().isocalendar().week}\n\n" + \
               "\n".join(f"{i+1}. [{s['category']}] {s['title']}\n" + "\n".join(f"   {t}" for t in s.get("tldr",[])) for i, s in enumerate(stories))
    send_email(html, plain)
    log.info("Done.")

if __name__ == "__main__":
    main()
