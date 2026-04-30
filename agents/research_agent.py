import os
import time
import re
import requests
from bs4 import BeautifulSoup
from utils.llm_utils import ask_llm
from utils.file_utils import save_text

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/sphinx",
}

# Guardian open API — 'test' is the public dev key (~12 calls/s, fine for low-volume use).
# For production volume, register at https://open-platform.theguardian.com/access/
GUARDIAN_API = "https://content.guardianapis.com/search"
GUARDIAN_KEY = os.environ.get("GUARDIAN_KEY", "test")


def _wikipedia_summary(name):
    """Fetch the Wikipedia intro for a player. Free, no key needed."""
    try:
        title = name.replace(" ", "_")
        url = (
            f"https://en.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&exsentences=30&exintro&explaintext"
            f"&titles={requests.utils.quote(title)}&format=json&redirects=1"
        )
        resp = requests.get(url, headers=HEADERS, timeout=10)
        pages = resp.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        extract = page.get("extract", "")
        if extract and len(extract) > 100:
            return extract
    except Exception as e:
        print(f"    [!] Wikipedia error for {name}: {e}")
    return ""


def _google_news_headlines(query, num=8):
    """
    Fetch real headlines from Google News RSS.
    Returns list of {title, url, source, date}.
    Free, no key needed.
    """
    headlines = []
    try:
        rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en&gl=GB&ceid=GB:en"
        resp = requests.get(rss_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "xml")
        for item in soup.select("item")[:num]:
            title = item.find("title")
            link = item.find("link")
            source = item.find("source")
            pub_date = item.find("pubDate")
            if title and link:
                headlines.append({
                    "title": title.get_text(strip=True),
                    "url": link.get_text(strip=True) if link else "",
                    "source": source.get_text(strip=True) if source else "",
                    "date": pub_date.get_text(strip=True) if pub_date else "",
                })
    except Exception as e:
        print(f"    [!] Google News RSS error: {e}")
    return headlines


def _guardian_articles(name: str, num: int = 6) -> list[dict]:
    """
    Fetch archival football journalism from The Guardian open API.
    Returns list of {title, url, source, date}.
    Free with 'test' API key (rate-limited but sufficient for research).
    """
    articles = []
    try:
        params = {
            "q":          f'"{name}" football',
            "section":    "football",
            "order-by":   "relevance",
            "page-size":  num,
            "api-key":    GUARDIAN_KEY,
            "show-fields":"headline,trailText",
        }
        resp = requests.get(GUARDIAN_API, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        for result in data.get("response", {}).get("results", []):
            articles.append({
                "title":  result.get("fields", {}).get("headline", result.get("webTitle", "")),
                "url":    result.get("webUrl", ""),
                "source": "The Guardian",
                "date":   result.get("webPublicationDate", "")[:10],
            })
    except Exception as e:
        print(f"    [!] Guardian API error for {name}: {e}")
    return articles


def _transfermarkt_history(name: str) -> str:
    """
    Scrape Transfermarkt for transfer history and market value data.
    Returns a summary string or empty string on failure.
    """
    try:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        # Search URL
        search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={requests.utils.quote(name)}"
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find first player result
        player_link = soup.select_one("table.items .hauptlink a")
        if not player_link:
            return ""

        # Follow to player page
        player_url = "https://www.transfermarkt.com" + player_link.get("href", "")
        resp2 = requests.get(player_url, headers=HEADERS, timeout=10)
        soup2 = BeautifulSoup(resp2.text, "html.parser")

        lines = []
        # Market value history from infobox
        mw_box = soup2.select_one(".tm-player-market-value-development__current-value")
        if mw_box:
            lines.append(f"Peak market value: {mw_box.get_text(strip=True)}")

        # Transfer table
        for row in soup2.select("#transferhistorie tbody tr")[:10]:
            cells = row.select("td")
            if len(cells) >= 5:
                season = cells[0].get_text(strip=True)
                from_club = cells[2].get_text(strip=True)
                to_club = cells[4].get_text(strip=True)
                fee_cell = cells[-1].get_text(strip=True)
                if season and (from_club or to_club):
                    lines.append(f"{season}: {from_club} → {to_club} ({fee_cell})")

        if lines:
            return "### Transfermarkt: " + name + "\n" + "\n".join(lines)
    except Exception as e:
        print(f"    [!] Transfermarkt error for {name}: {e}")
    return ""


def _format_headlines(headlines):
    if not headlines:
        return "No headlines found."
    lines = []
    for h in headlines:
        lines.append(f'- "{h["title"]}" — {h["source"]} [{h["url"]}]')
    return "\n".join(lines)


def conduct_research(entities, output_dir, is_comparison=False):
    print(f"[*] Research Agent compiling data for: {entities}")

    names = [n.strip() for n in entities.split(",")]

    # Gather real source material
    wiki_sections = []
    all_headlines = []

    transfermarkt_sections = []

    for name in names:
        print(f"    -> Wikipedia: {name}")
        wiki = _wikipedia_summary(name)
        if wiki:
            wiki_sections.append(f"### Wikipedia: {name}\n{wiki[:3000]}")
        time.sleep(1)

        print(f"    -> News headlines: {name}")
        headlines = _google_news_headlines(f"{name} football documentary", num=5)
        headlines += _google_news_headlines(f"{name} career interview", num=3)
        all_headlines.extend(headlines)
        time.sleep(1)

        print(f"    -> Guardian archive: {name}")
        guardian = _guardian_articles(name, num=6)
        all_headlines.extend(guardian)
        time.sleep(0.5)

        print(f"    -> Transfermarkt: {name}")
        tm = _transfermarkt_history(name)
        if tm:
            transfermarkt_sections.append(tm)
        time.sleep(1)

    # Also search for the full topic
    topic_headlines = _google_news_headlines(entities + " football", num=5)
    all_headlines.extend(topic_headlines)

    wiki_text = "\n\n".join(wiki_sections) if wiki_sections else "No Wikipedia data found."
    tm_text = "\n\n".join(transfermarkt_sections) if transfermarkt_sections else ""
    headline_text = _format_headlines(all_headlines)

    # Build quality score
    sources_used = []
    if wiki_sections: sources_used.append("Wikipedia")
    if guardian: sources_used.append("Guardian")
    if transfermarkt_sections: sources_used.append("Transfermarkt")
    if all_headlines: sources_used.append("Google News")
    quality = "high" if len(sources_used) >= 3 else "medium" if len(sources_used) >= 2 else "low"
    print(f"    [Research] Quality: {quality} — sources: {', '.join(sources_used) or 'none'}")

    transfer_section = f"\nTRANSFER HISTORY:\n{tm_text}" if tm_text else ""

    if is_comparison:
        prompt = f"""
You are a researcher for a high-prestige football documentary channel.

Using ONLY the real source material below, create a Comparative Research Dossier for: {entities}

SOURCE MATERIAL:
{wiki_text}
{transfer_section}

REAL HEADLINES (include the URL next to each headline you reference):
{headline_text}

REQUIRED SECTIONS:
# COMPARISON DOSSIER

## Head-to-Head Overview
## Career Statistics (only facts confirmed in the source material above)
## Defining Moments (specific matches, with dates and results)
## Key Controversies
## Legacy and Impact

RULES:
- Do not invent statistics. Only use numbers that appear in the source material.
- Where you cite a headline, include it in the format: [Source: headline text](URL)
- Flag any claim you are uncertain about with (UNVERIFIED).
"""
        filename = "comparison_dossier.md"

    else:
        prompt = f"""
You are a researcher for a high-prestige football documentary channel.

Using ONLY the real source material below, create a Research Dossier for: {entities}

SOURCE MATERIAL:
{wiki_text}
{transfer_section}

REAL HEADLINES (include the URL next to each headline you reference):
{headline_text}

REQUIRED SECTIONS:
# KEY MOMENTS (specific matches with dates, opponents, and results)
# CONTROVERSIES (documented incidents only — cite the source)
# NARRATIVE THEMES (the emotional through-lines of their career)
# STORY ARC (rise, peak, decline — grounded in real events)
# QUOTES (only quotes that appear in the source material above)

RULES:
- Do not invent statistics. Only use numbers that appear in the source material.
- Where you cite a headline, include it in the format: [Source: headline text](URL)
- Flag any claim you are uncertain about with (UNVERIFIED).
"""
        filename = "research.md"

    res = ask_llm(prompt)
    save_text(f"{output_dir}/{filename}", res)
    return res
