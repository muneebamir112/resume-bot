# -*- coding: utf-8 -*-
"""Fetch a job description AND the hiring company name from a URL, and save
the JD as a plain-text file in the same format as the existing jd_*.txt
files (e.g. jd_whippy.txt), so it can be fed into ollama_generate.py or
read manually.

Usage:
    python fetch_jd.py <url> [output_name]

Company name detection order:
    1. schema.org JobPosting JSON-LD (hiringOrganization.name) — used by
       most ATS platforms (Greenhouse, Lever, Workday, etc.)
    2. og:site_name / application-name meta tags
    3. Parsed from the page <title> (e.g. "Job Title at Company")
    4. Domain name, as a last resort

output_name (if given) overrides the detected company name for the
filename; the detected company name is still reported separately.

Note: this does a plain HTTP fetch + HTML text extraction. Job boards that
render the description via JavaScript (some Workday/LinkedIn pages) may
return incomplete text or miss the JSON-LD block — paste the JD manually
in that case.
"""

import sys
import os
import re
import json
import time
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

# Windows' console defaults to a codepage (e.g. cp1252) that can't encode
# emoji/CJK/etc. that show up in real job titles - without this, print()
# raises UnicodeEncodeError and the whole fetch is wrongly reported as a
# failure even though the JD was already fetched and saved successfully.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

STRIP_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "svg", "img"]

# Below this, extracted text is treated as not-really-there (JS-rendered
# board, consent wall, 404 shell) and worth retrying with a real browser.
MIN_TEXT_CHARS = 200

# Transient DNS/connection blips (getaddrinfo failures, resets) shouldn't
# permanently lose a job — retry a few times with backoff before giving up.
FETCH_RETRIES = 4
FETCH_RETRY_DELAY = 5


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text[:60] or "job"


def clean_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    deduped = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return "\n".join(deduped)


def find_job_posting(node):
    """Walk parsed JSON-LD data (dict or list, possibly nested in @graph)
    looking for a schema.org JobPosting node."""
    if isinstance(node, list):
        for item in node:
            found = find_job_posting(item)
            if found:
                return found
        return None
    if isinstance(node, dict):
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        if "JobPosting" in types:
            return node
        if "@graph" in node:
            return find_job_posting(node["@graph"])
    return None


def extract_from_jsonld(soup):
    """Return (company, job_title, description_text) from schema.org
    JobPosting JSON-LD, or (None, None, None) if not present/parseable."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.text
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        posting = find_job_posting(data)
        if not posting:
            continue

        org = posting.get("hiringOrganization")
        company = None
        if isinstance(org, dict):
            company = org.get("name")
        elif isinstance(org, str):
            company = org

        job_title = posting.get("title")

        description = posting.get("description")
        description_text = None
        if description:
            description_text = clean_lines(
                BeautifulSoup(description, "html.parser").get_text("\n")
            )

        if company or job_title or description_text:
            return company, job_title, description_text
    return None, None, None


def extract_company_from_meta(soup):
    for prop in ("og:site_name", "application-name"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def extract_company_from_title(title):
    if not title:
        return None
    for sep in (" at ", " - ", " | "):
        if sep in title:
            parts = title.split(sep)
            if len(parts) >= 2:
                return parts[-1].strip()
    return None


def extract_company_from_domain(url):
    host = urlparse(url).netloc
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return host


def extract_page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    return title, clean_lines(soup.get_text("\n"))


def fetch(url):
    """GET the page, retrying transient connection/DNS failures a few times
    with backoff before giving up - a single DNS resolver blip shouldn't
    permanently skip a job."""
    last_err = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            if attempt < FETCH_RETRIES:
                print(f"  Fetch attempt {attempt}/{FETCH_RETRIES} failed ({e.__class__.__name__}), "
                      f"retrying in {FETCH_RETRY_DELAY}s...")
                time.sleep(FETCH_RETRY_DELAY)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 410):
                print(f"Error: The job link appears to be expired ({e.response.status_code}).")
                sys.exit(1)
            raise
    raise last_err


def fetch_rendered(url):
    """Fallback for JS-rendered job boards (Workday, some Greenhouse/Lever
    embeds): load the page in a real (headless) browser and return the
    rendered HTML, instead of the raw pre-JS response requests.get() sees."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            # Some SPAs (e.g. app.dover.com) keep a background connection
            # open forever and never reach "networkidle", which would make
            # this raise instead of returning the page it already rendered.
            # "domcontentloaded" is enough for the JS framework to have
            # mounted; the explicit wait below lets it finish painting.
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)  # let JS render + late XHR content settle
            return page.content()
        finally:
            browser.close()


def extract(html, url):
    soup = BeautifulSoup(html, "html.parser")

    company, job_title, jsonld_text = extract_from_jsonld(BeautifulSoup(html, "html.parser"))
    page_title, page_text = extract_page_text(html)

    if not company:
        company = extract_company_from_meta(soup)
    if not company:
        company = extract_company_from_title(page_title)
    if not company:
        company = extract_company_from_domain(url)

    text = jsonld_text if jsonld_text and len(jsonld_text) > 200 else page_text

    return company.strip(), job_title, text


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python fetch_jd.py <url> [output_name]")
        sys.exit(1)

    url = sys.argv[1]
    out_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"Fetching: {url}")
    html = fetch(url)
    company, job_title, text = extract(html, url)

    if len(text) < MIN_TEXT_CHARS:
        print("Extracted text is very short - page likely renders its content "
              "via JavaScript. Retrying with a headless browser...")
        try:
            html = fetch_rendered(url)
            company2, job_title2, text2 = extract(html, url)
            if len(text2) > len(text):
                company, job_title, text = company2, job_title2, text2
        except Exception as e:
            print(f"Headless-browser retry failed: {e}")

    if len(text) < MIN_TEXT_CHARS:
        print("WARNING: extracted text is still very short after the headless-"
              "browser retry - the JD may be behind a login/consent wall. "
              "Consider pasting the JD manually instead.")

    name = sys.argv[2] if len(sys.argv) == 3 else company
    slug = slugify(name)

    out_path = os.path.join(out_dir, f"jd_{slug}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Company: {company}")
    if job_title:
        print(f"Job title: {job_title}")
    print(f"Saved: {out_path}")
    print(f"Extracted {len(text)} characters.")


if __name__ == "__main__":
    main()
