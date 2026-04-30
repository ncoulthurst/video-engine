"""
Fact Check Agent — verifies claims from source_agent.py against their URLs.

After source_agent.py finds URLs for key claims, this agent:
1. Fetches each URL
2. Checks if the claim appears in the page content (substring + semantic match via LLM)
3. Marks each claim: verified / unverified / contradicted
4. Logs any contradicted claims prominently
5. Writes fact_check.json to the output directory

Usage:
    from agents.fact_check_agent import run_fact_check
    result = run_fact_check(output_dir)

Returns:
    {
        "claims": [
            {
                "claim": "Suárez scored 31 goals in 2013/14",
                "url": "https://...",
                "status": "verified" | "unverified" | "contradicted",
                "evidence": "...snippet from page...",
                "contradiction": "...conflicting text if any..."
            }
        ],
        "verified_count": 3,
        "unverified_count": 1,
        "contradicted_count": 0,
        "summary": "3/4 claims verified. 0 contradictions found."
    }
"""

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from utils.llm_utils import ask_llm
from utils.file_utils import save_text

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/sphinx",
}

MAX_PAGE_CHARS = 3000
FETCH_TIMEOUT  = 12


def _fetch_page_text(url: str) -> str:
    """Fetch a URL and extract plain text. Returns '' on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, nav
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text[:MAX_PAGE_CHARS]
    except Exception as e:
        print(f"    [FactCheck] ✗ Fetch failed for {url}: {e}")
        return ""


def _check_claim(claim: str, page_text: str, url: str) -> dict:
    """
    Use LLM to check if the page text supports, contradicts, or is silent on the claim.
    Returns dict with status, evidence, contradiction.
    """
    if not page_text:
        return {
            "status":        "unverified",
            "evidence":      "",
            "contradiction": "",
            "reason":        "Page could not be fetched",
        }

    prompt = f"""You are a fact-checker for a football documentary. Check whether the page content supports or contradicts the claim.

CLAIM: "{claim}"

PAGE CONTENT (from {url}):
{page_text}

Reply ONLY with JSON in this exact format:
{{
  "status": "verified" | "unverified" | "contradicted",
  "evidence": "exact quote from page that supports the claim (or empty string)",
  "contradiction": "exact quote that contradicts the claim (or empty string)",
  "reason": "one-sentence explanation"
}}

Rules:
- "verified": page explicitly confirms the claim with specific text
- "unverified": page doesn't mention the claim or is ambiguous
- "contradicted": page gives a different figure/fact that conflicts with the claim
"""

    try:
        raw = ask_llm(prompt, expect_json=True).strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.split("\n")
                if not line.strip().startswith("```")
            ).strip()
        result = json.loads(raw)
        return {
            "status":        result.get("status", "unverified"),
            "evidence":      result.get("evidence", ""),
            "contradiction": result.get("contradiction", ""),
            "reason":        result.get("reason", ""),
        }
    except Exception as e:
        print(f"    [FactCheck] LLM check failed: {e}")
        return {
            "status":        "unverified",
            "evidence":      "",
            "contradiction": "",
            "reason":        f"LLM check failed: {e}",
        }


def _load_sources(output_dir: str) -> list[dict]:
    """
    Load claims + URLs from sources.json (written by source_agent.py).
    Falls back to parsing sources.md if JSON is unavailable.
    Returns list of {claim, url}.
    """
    json_path = Path(output_dir) / "sources.json"
    md_path   = Path(output_dir) / "sources.md"

    if json_path.exists():
        try:
            with open(json_path) as f:
                data = json.load(f)
            # source_agent writes either a list or {"claims": [...]}
            claims = data if isinstance(data, list) else data.get("claims", [])
            return [c for c in claims if c.get("claim") and c.get("url")]
        except Exception:
            pass

    # Fall back to parsing sources.md
    if md_path.exists():
        text = md_path.read_text(encoding="utf-8")
        results = []
        # Pattern: "- **Claim** [Source](URL)" or "- Claim — URL"
        for m in re.finditer(r"[-*]\s*(.+?)\s*[\[（]\S+[\]）]\s*(https?://\S+)", text):
            results.append({"claim": m.group(1).strip("*_ "), "url": m.group(2)})
        # Also match bare "claim: URL" lines
        for m in re.finditer(r"(.{20,120}?):\s*(https?://\S+)", text):
            results.append({"claim": m.group(1).strip(), "url": m.group(2)})
        return results[:10]  # cap at 10 to avoid runaway

    return []


def run_fact_check(output_dir: str) -> dict:
    """
    Main entry point. Reads sources from output_dir, fetches each URL,
    and checks the claim. Writes fact_check.json. Returns summary dict.
    """
    print(f"[*] Fact Check Agent running on: {output_dir}")

    sources = _load_sources(output_dir)
    if not sources:
        print("    [FactCheck] No sources found — skipping fact check")
        return {
            "claims": [],
            "verified_count": 0,
            "unverified_count": 0,
            "contradicted_count": 0,
            "summary": "No sources available to check.",
        }

    print(f"    [FactCheck] Checking {len(sources)} claims...")

    results = []
    for i, src in enumerate(sources):
        claim = src.get("claim", "")
        url   = src.get("url", "")
        if not claim or not url:
            continue

        print(f"    [FactCheck] {i+1}/{len(sources)}: {claim[:60]}…")

        page_text = _fetch_page_text(url)
        time.sleep(0.5)  # polite crawl

        check = _check_claim(claim, page_text, url)
        status = check["status"]

        entry = {
            "claim":         claim,
            "url":           url,
            "status":        status,
            "evidence":      check["evidence"],
            "contradiction": check["contradiction"],
            "reason":        check["reason"],
        }
        results.append(entry)

        icon = {"verified": "✓", "unverified": "?", "contradicted": "✗"}.get(status, "?")
        print(f"    [FactCheck] {icon} {status}: {claim[:60]}")

        if status == "contradicted":
            print(f"    [FactCheck] ⚠ CONTRADICTION: {check['contradiction'][:120]}")

        time.sleep(0.3)

    # Aggregate
    verified_count     = sum(1 for r in results if r["status"] == "verified")
    unverified_count   = sum(1 for r in results if r["status"] == "unverified")
    contradicted_count = sum(1 for r in results if r["status"] == "contradicted")
    total              = len(results)

    summary = (
        f"{verified_count}/{total} claims verified. "
        f"{contradicted_count} contradiction{'s' if contradicted_count != 1 else ''} found."
    )

    if contradicted_count > 0:
        print(f"\n    [FactCheck] ⚠ WARNING: {contradicted_count} contradicted claim(s) — review fact_check.json before publishing\n")

    output = {
        "claims":             results,
        "verified_count":     verified_count,
        "unverified_count":   unverified_count,
        "contradicted_count": contradicted_count,
        "summary":            summary,
    }

    # Save to file
    out_path = Path(output_dir) / "fact_check.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"    [FactCheck] ✓ Wrote {out_path}")

    # Also write a human-readable summary
    md_lines = [f"# Fact Check Report\n", f"**{summary}**\n"]
    for r in results:
        icon = {"verified": "✅", "unverified": "❓", "contradicted": "❌"}.get(r["status"], "❓")
        md_lines.append(f"\n{icon} **{r['claim']}**")
        md_lines.append(f"   Source: {r['url']}")
        if r["evidence"]:
            md_lines.append(f"   Evidence: _{r['evidence'][:120]}_")
        if r["contradiction"]:
            md_lines.append(f"   ⚠ Contradiction: _{r['contradiction'][:120]}_")
        md_lines.append(f"   Reason: {r['reason']}")

    save_text(str(Path(output_dir) / "fact_check.md"), "\n".join(md_lines))

    return output
