"""
Weekly Job Search Digest
Scrapes job listings from multiple sources and sends a formatted email digest.
Target roles: Operations, Product Management (mid-level through Director/VP+)
Location: Remote or Hybrid (NJ/NY based)
"""

import os
import json
import time
import base64
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ─── Config ────────────────────────────────────────────────────────────────────

SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
RECIPIENT_EMAIL  = os.environ["RECIPIENT_EMAIL"]
SERPAPI_KEY      = os.environ.get("SERPAPI_KEY", "")

SENIORITY_LEVELS = ["mid-level", "senior", "director", "VP", "head of"]

ROLES = [
    "Operations Manager",
    "Senior Operations Manager",
    "Director of Operations",
    "Director of Operations and Strategy",
    "Operations and Strategy Manager",
    "Senior Operations and Strategy Manager",
    "Product Manager",
    "Senior Product Manager",
    "Director of Product Management",
    "Director of Product",
    "Product Operations Manager",
    "Senior Product Operations Manager",
    "Director of Product Operations",
    "Technical Program Manager",
    "Senior Technical Program Manager",
    "Strategy and Operations Manager",
    "Senior Strategy and Operations Manager",
    "Director of Strategy and Operations",
    "Business Operations Manager",
    "Senior Business Operations Manager",
    "Director of Business Operations",
]

LOCATION_TERMS = [
    "remote",
    "hybrid New York",
    "hybrid New Jersey",
    "hybrid NYC",
    "hybrid NJ",
    "hybrid NY",
]

# ─── Gmail Auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    """Build an authenticated Gmail API service using stored OAuth credentials."""
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build("gmail", "v1", credentials=creds)


# ─── Job Scraping ──────────────────────────────────────────────────────────────

def search_jobs_serpapi(query: str, location: str = "") -> list[dict]:
    """Use SerpApi Google Jobs endpoint to search for listings."""
    if not SERPAPI_KEY:
        return []

    params = {
        "engine": "google_jobs",
        "q": query,
        "api_key": SERPAPI_KEY,
        "chips": "date_posted:month",  # past 30 days
        "hl": "en",
    }
    if location:
        params["location"] = location

    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("jobs_results", [])
    except Exception as e:
        print(f"SerpApi error for '{query}': {e}")
        return []


def search_remotive(role_keyword: str) -> list[dict]:
    """Search Remotive.com public API for remote tech jobs."""
    try:
        resp = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": role_keyword, "limit": 20},
            timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        # Filter to past 30 days
        cutoff = datetime.utcnow() - timedelta(days=30)
        results = []
        for j in jobs:
            pub = j.get("publication_date", "")
            try:
                pub_dt = datetime.fromisoformat(pub[:10])
                if pub_dt >= cutoff:
                    results.append(j)
            except Exception:
                results.append(j)
        return results
    except Exception as e:
        print(f"Remotive error for '{role_keyword}': {e}")
        return []


def search_themuse(role_keyword: str) -> list[dict]:
    """Search The Muse public API — free, no key required."""
    try:
        resp = requests.get(
            "https://www.themuse.com/api/public/jobs",
            params={
                "descending": "true",
                "page": 0,
                "category": "Project & Product Management,Operations & Logistics",
            },
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        # Filter by keyword match in title
        keyword_lower = role_keyword.lower()
        matched = [j for j in results if keyword_lower in j.get("name", "").lower()]
        return matched
    except Exception as e:
        print(f"The Muse error for '{role_keyword}': {e}")
        return []


def normalize_themuse_job(raw: dict) -> dict:
    """Normalize a The Muse job result."""
    company = raw.get("company", {}).get("name", "N/A")
    locations = raw.get("locations", [])
    location = ", ".join(loc.get("name", "") for loc in locations) if locations else "Remote"
    levels = raw.get("levels", [])
    level_str = ", ".join(lv.get("name", "") for lv in levels) if levels else ""
    title = raw.get("name", "N/A")
    if level_str:
        title = f"{title} ({level_str})"
    pub_date = raw.get("publication_date", "")[:10] if raw.get("publication_date") else "N/A"
    return {
        "title": title,
        "company": company,
        "location": location,
        "salary": "Not listed",
        "posted": pub_date,
        "description": summarize_description(raw.get("contents") or "", company),
        "apply_link": raw.get("refs", {}).get("landing_page", "#"),
        "hiring_contact": find_hiring_contact(company),
        "company_website": get_company_website(company),
        "source": "The Muse",
    }


def search_jobicy(role_keyword: str) -> list[dict]:
    """Search Jobicy public API — free, no key required, remote-only tech jobs."""
    try:
        resp = requests.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params={
                "count": 20,
                "tag": role_keyword,
                "industry": "tech",
            },
            timeout=15,
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        # Filter to past 30 days
        cutoff = datetime.utcnow() - timedelta(days=30)
        results = []
        for j in jobs:
            pub = j.get("pubDate", "")
            try:
                pub_dt = datetime.strptime(pub[:10], "%Y-%m-%d")
                if pub_dt >= cutoff:
                    results.append(j)
            except Exception:
                results.append(j)
        return results
    except Exception as e:
        print(f"Jobicy error for '{role_keyword}': {e}")
        return []


def normalize_jobicy_job(raw: dict) -> dict:
    """Normalize a Jobicy job result."""
    company = raw.get("companyName", "N/A")
    return {
        "title": raw.get("jobTitle", "N/A"),
        "company": company,
        "location": raw.get("jobGeo", "Remote"),
        "salary": raw.get("annualSalaryMin") and raw.get("annualSalaryMax") and
                  f"${raw['annualSalaryMin']:,} – ${raw['annualSalaryMax']:,}" or "Not listed",
        "posted": raw.get("pubDate", "N/A")[:10],
        "description": summarize_description(raw.get("jobDescription") or "", company),
        "apply_link": raw.get("url", "#"),
        "hiring_contact": find_hiring_contact(company),
        "company_website": get_company_website(company),
        "source": "Jobicy",
    }


def search_linkedin_rss(role_keyword: str, location: str = "United States") -> list[dict]:
    """
    Pull LinkedIn Jobs via their public RSS feed — no auth required.
    Returns raw parsed entries.
    """
    import xml.etree.ElementTree as ET
    try:
        keyword_encoded = requests.utils.quote(role_keyword)
        location_encoded = requests.utils.quote(location)
        url = (
            f"https://www.linkedin.com/jobs/search/?keywords={keyword_encoded}"
            f"&location={location_encoded}"
            f"&f_WT=2,3"       # 2=remote, 3=hybrid
            f"&f_TPR=r2592000"  # past 30 days
            f"&position=1&pageNum=0"
        )
        # LinkedIn RSS feed endpoint
        rss_url = f"https://www.linkedin.com/jobs/search.rss?keywords={keyword_encoded}&location={location_encoded}&f_WT=2,3&f_TPR=r2592000"
        resp = requests.get(rss_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        results = []
        for item in items:
            title = item.findtext("title", "N/A")
            link = item.findtext("link", "#")
            desc = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "N/A")
            # Parse company and location from title (LinkedIn format: "Title at Company · Location")
            company = "N/A"
            location_str = "Remote"
            if " at " in title:
                parts = title.split(" at ", 1)
                role_part = parts[0].strip()
                rest = parts[1].strip()
                if " · " in rest:
                    co_loc = rest.split(" · ", 1)
                    company = co_loc[0].strip()
                    location_str = co_loc[1].strip()
                else:
                    company = rest
            else:
                role_part = title
            results.append({
                "title": role_part,
                "company": company,
                "location": location_str,
                "description": desc,
                "apply_link": link,
                "posted": pub_date,
            })
        return results
    except Exception as e:
        print(f"LinkedIn RSS error for '{role_keyword}': {e}")
        return []


def normalize_linkedin_rss_job(raw: dict) -> dict:
    """Normalize a LinkedIn RSS job entry."""
    company = raw.get("company", "N/A")
    return {
        "title": raw.get("title", "N/A"),
        "company": company,
        "location": raw.get("location", "Remote"),
        "salary": "Not listed",
        "posted": raw.get("posted", "N/A"),
        "description": summarize_description(raw.get("description") or "", company),
        "apply_link": raw.get("apply_link", "#"),
        "hiring_contact": find_hiring_contact(company),
        "company_website": get_company_website(company),
        "source": "LinkedIn",
    }


def search_adzuna(role_keyword: str) -> list[dict]:
    """Search Adzuna public API (US, no key needed for basic usage)."""
    try:
        url = (
            f"https://api.adzuna.com/v1/api/jobs/us/search/1"
            f"?app_id=test&app_key=test"  # Replace with real keys for production
            f"&results_per_page=20"
            f"&what={requests.utils.quote(role_keyword)}"
            f"&where=remote"
            f"&max_days_old=30"
        )
        resp = requests.get(url, timeout=15)
        # Adzuna may require real keys; gracefully skip if auth fails
        if resp.status_code in (401, 403):
            return []
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"Adzuna error for '{role_keyword}': {e}")
        return []


def summarize_description(description: str, company_name: str) -> str:
    """
    Return a tight 3-sentence max snippet: 1-2 sentences on the company,
    1-2 sentences on the role. Hard cap of 3 sentences total.
    """
    if not description:
        return "No description available."

    import re
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", description)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if not sentences:
        return "No description available."

    company_lower = company_name.lower() if company_name else ""
    company_keywords = {"we are", "we're", "our company", "founded", "platform",
                        "mission", "we build", "we help", "we provide", company_lower}
    role_keywords = {"you will", "you'll", "responsibilities", "looking for",
                     "candidate", "this role", "the role", "you'll own", "you'll lead"}

def summarize_description(description: str, company_name: str) -> str:
    """
    Return a tight snippet capped at 200 characters max.
    Picks the best 1 sentence about the company and 1 about the role.
    """
    if not description:
        return "No description available."

    import re
    clean = re.sub(r"<[^>]+>", " ", description)
    clean = re.sub(r"\s+", " ", clean).strip()

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if not sentences:
        return "No description available."

    company_lower = company_name.lower() if company_name else ""
    company_keywords = {"we are", "we're", "our company", "founded", "platform",
                        "mission", "we build", "we help", "we provide", company_lower}
    role_keywords = {"you will", "you'll", "responsibilities", "looking for",
                     "candidate", "this role", "the role", "you'll own", "you'll lead"}

    company_sent = next((s for s in sentences if any(kw in s.lower() for kw in company_keywords)), None)
    role_sent = next((s for s in sentences if any(kw in s.lower() for kw in role_keywords)), None)

    if company_sent and role_sent and company_sent != role_sent:
        result = f"{company_sent} {role_sent}"
    elif company_sent:
        result = company_sent
    elif role_sent:
        result = role_sent
    else:
        result = sentences[0]

    # Hard cap at 200 characters — cut cleanly at last word boundary
    if len(result) > 200:
        result = result[:197].rsplit(" ", 1)[0] + "..."

    return result


def get_direct_apply_link(raw: dict) -> str:
    """
    Extract the best direct apply link from a SerpApi job result.
    Prefer apply_options (direct job board links) over related_links (often Google search pages).
    """
    # apply_options contains direct links to the job on LinkedIn, Indeed, etc.
    apply_options = raw.get("apply_options") or []
    for option in apply_options:
        link = option.get("link", "")
        # Prefer well-known job boards with direct listings
        for preferred in ["linkedin.com/jobs", "indeed.com/viewjob", "greenhouse.io",
                          "lever.co", "workday.com", "myworkdayjobs.com", "jobvite.com",
                          "smartrecruiters.com", "ashbyhq.com", "careers."]:
            if preferred in link:
                return link
    # Fall back to first apply_option if no preferred source found
    if apply_options:
        return apply_options[0].get("link", "#")
    # Last resort: job_id based Google Jobs link (better than a search page)
    job_id = raw.get("job_id", "")
    if job_id:
        return f"https://www.google.com/search?q={requests.utils.quote(raw.get('title',''))}+{requests.utils.quote(raw.get('company_name',''))}&ibp=htl;jobs&htidocid={job_id}"
    return "#"


def get_company_website(company_name: str) -> str:
    """Return a direct link to the company's careers page or website."""
    if not company_name or company_name == "N/A":
        return "#"
    # Build a Google search URL targeting the company's careers page
    query = requests.utils.quote(f"{company_name} careers site")
    return f"https://www.google.com/search?q={query}"


def normalize_serpapi_job(raw: dict) -> dict:
    """Normalize a SerpApi job result into our standard schema."""
    extensions = raw.get("detected_extensions", {})
    company = raw.get("company_name", "N/A")
    return {
        "title": raw.get("title", "N/A"),
        "company": company,
        "location": raw.get("location", "N/A"),
        "salary": extensions.get("salary", "Not listed"),
        "posted": extensions.get("posted_at", "N/A"),
        "description": summarize_description(raw.get("description") or "", raw.get("company_name", "")),
        "apply_link": get_direct_apply_link(raw),
        "hiring_contact": find_hiring_contact(company),
        "company_website": get_company_website(company),
        "source": "Google Jobs",
    }


def normalize_remotive_job(raw: dict) -> dict:
    """Normalize a Remotive job result."""
    company = raw.get("company_name", "N/A")
    return {
        "title": raw.get("title", "N/A"),
        "company": company,
        "location": raw.get("candidate_required_location", "Remote"),
        "salary": raw.get("salary", "Not listed") or "Not listed",
        "posted": raw.get("publication_date", "N/A")[:10],
        "description": summarize_description(raw.get("description") or "", raw.get("company_name", "")),
        "apply_link": raw.get("url", "#"),
        "hiring_contact": find_hiring_contact(company),
        "company_website": get_company_website(company),
        "source": "Remotive",
    }


def find_hiring_contact(company_name: str) -> str:
    """
    Return a LinkedIn people search URL scoped to the exact company,
    filtered to recruiter / HR / talent acquisition roles.
    Uses LinkedIn's currentCompany + keywords filters for precision.
    """
    if not company_name or company_name == "N/A":
        return "#"
    # LinkedIn people search with title keywords and company name scoped in
    # keywords field. The `titleFreeText` param filters by current job title.
    keywords = requests.utils.quote("recruiter OR \"talent acquisition\" OR \"HR business partner\" OR \"human resources\"")
    company_encoded = requests.utils.quote(company_name)
    return (
        f"https://www.linkedin.com/search/results/people/"
        f"?keywords={keywords}"
        f"&titleFreeText=recruiter%20OR%20talent%20OR%20HR"
        f"&company={company_encoded}"
        f"&origin=FACETED_SEARCH"
    )


# ─── Deduplication ─────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    replacements = {
        "sr.": "senior", "sr ": "senior ", "vp": "vice president",
        "mgr": "manager", "dir.": "director", "dir ": "director ",
        "&": "and", "-": " ", "/": " "
    }
    text = text.lower().strip()
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove extra spaces
    return " ".join(text.split())


def titles_are_similar(t1: str, t2: str) -> bool:
    """Return True if two job titles are close enough to be considered duplicates."""
    n1, n2 = normalize_text(t1), normalize_text(t2)
    if n1 == n2:
        return True
    # Check if one is a substring of the other (e.g. "Product Manager" vs "Senior Product Manager")
    if n1 in n2 or n2 in n1:
        return True
    return False


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Remove duplicate listings using fuzzy title + exact company matching."""
    unique = []
    for job in jobs:
        company_key = normalize_text(job["company"])
        is_dup = False
        for existing in unique:
            if normalize_text(existing["company"]) == company_key:
                if titles_are_similar(existing["title"], job["title"]):
                    is_dup = True
                    break
        if not is_dup:
            unique.append(job)
    return unique


# ─── Orchestration ─────────────────────────────────────────────────────────────

def collect_all_jobs() -> list[dict]:
    all_jobs = []

    # ── SerpApi (Google Jobs) searches ──────────────────────────────────────────
    if SERPAPI_KEY:
        serpapi_queries = [
            # Operations roles
            ("senior operations manager tech company remote OR hybrid", ""),
            ("director of operations technology remote OR hybrid New York New Jersey", ""),
            ("strategy and operations manager tech remote hybrid", ""),
            ("director strategy operations tech remote hybrid NJ NY", ""),
            ("product operations manager senior tech remote hybrid", ""),
            # Product roles
            ("senior product manager tech company remote", ""),
            ("director of product management remote OR hybrid NY NJ", ""),
            ("director of product remote hybrid New York", ""),
            # Blend roles
            ("product operations director remote hybrid NJ NY", ""),
            ("technical program manager senior remote hybrid", ""),
            ("business operations manager senior tech remote", ""),
            ("director business operations tech remote hybrid NY NJ", ""),
            # Fractional / contract
            ("fractional product manager tech remote", ""),
            ("fractional operations manager remote", ""),
            ("fractional chief of staff tech remote", ""),
            ("contract product manager senior remote", ""),
            ("interim operations director remote", ""),
        ]
        for query, loc in serpapi_queries:
            raw_jobs = search_jobs_serpapi(query, loc)
            for r in raw_jobs:
                all_jobs.append(normalize_serpapi_job(r))
            time.sleep(1)  # polite rate limiting

    # ── Remotive (free, no key needed) ──────────────────────────────────────────
    remotive_searches = [
        "product manager",
        "operations manager",
        "product operations",
        "director product",
        "strategy and operations",
        "technical program manager",
    ]
    for kw in remotive_searches:
        raw_jobs = search_remotive(kw)
        for r in raw_jobs:
            all_jobs.append(normalize_remotive_job(r))
        time.sleep(0.5)

    # ── The Muse (free, no key needed) ──────────────────────────────────────────
    themuse_searches = [
        "product manager",
        "senior product manager",
        "director of product",
        "operations manager",
        "director of operations",
        "product operations",
        "technical program manager",
        "strategy and operations",
        "business operations",
    ]
    for kw in themuse_searches:
        raw_jobs = search_themuse(kw)
        for r in raw_jobs:
            all_jobs.append(normalize_themuse_job(r))
        time.sleep(0.5)

    # ── Jobicy (free, no key needed) ─────────────────────────────────────────────
    jobicy_searches = [
        "product",
        "operations",
        "strategy",
        "manager",
        "director",
    ]
    for kw in jobicy_searches:
        raw_jobs = search_jobicy(kw)
        for r in raw_jobs:
            all_jobs.append(normalize_jobicy_job(r))
        time.sleep(0.5)

    # ── LinkedIn RSS (free, no key needed) ───────────────────────────────────────
    linkedin_searches = [
        ("senior product manager", "Remote"),
        ("director of product", "New York"),
        ("director of product", "New Jersey"),
        ("senior operations manager", "Remote"),
        ("director of operations", "New York"),
        ("director of operations", "New Jersey"),
        ("product operations manager", "Remote"),
        ("strategy and operations manager", "Remote"),
        ("director strategy and operations", "New York"),
        ("technical program manager", "Remote"),
        ("business operations manager senior", "Remote"),
    ]
    for kw, loc in linkedin_searches:
        raw_jobs = search_linkedin_rss(kw, loc)
        for r in raw_jobs:
            all_jobs.append(normalize_linkedin_rss_job(r))
        time.sleep(1)

    return deduplicate(all_jobs)


def filter_jobs(jobs: list[dict]) -> list[dict]:
    """
    Keep jobs that:
    - Are remote, OR hybrid in NJ/NY
    - Match target seniority (mid-level through Director)
    - Are in the right role categories
    """
    location_keywords = {
        "remote", "anywhere", "hybrid", "new york", "new jersey", "ny", "nj", "nyc"
    }
    seniority_keywords = {
        "senior", "sr.", "sr ", "director", "lead", "principal", "manager",
        "strategy", "operations & strategy", "strategy & operations",
        "strategy and operations", "operations and strategy",
    }
    exclude_seniority = {"vp", "vice president", "head of", "chief", "coo", "cto", "cpo"}
    exclude_keywords = {
        "retail", "restaurant", "food service", "hospitality", "healthcare clinic",
        "manufacturing", "warehouse", "logistics driver"
    }

    filtered = []
    for job in jobs:
        loc = job["location"].lower().strip()
        title = job["title"].lower()
        desc = job["description"].lower()
        company = job["company"].lower()

        # Fractional roles bypass location/seniority filters
        if job.get("is_fractional"):
            combined = f"{company} {desc}"
            if not any(kw in combined for kw in exclude_keywords):
                filtered.append(job)
            continue

        # ── Location check — only inspect the location field ──────────────────
        loc_clean = loc.strip()

        # Case 1: Fully remote — accept regardless of any other location detail
        is_remote = any(kw in loc_clean for kw in {
            "remote", "anywhere", "distributed", "work from home", "wfh"
        })

        # Case 2: Hybrid — must mention NJ or NY (any town, city, or borough)
        # Matches: "Hybrid - New York, NY", "Hybrid (Hoboken, NJ)", "Jersey City, NJ (Hybrid)", etc.
        in_nj_or_ny = any(kw in loc_clean for kw in {
            "new york", "new jersey", ", ny", ", nj", "(ny)", "(nj)",
            "nyc", "brooklyn", "queens", "bronx", "staten island",
            "manhattan", "hoboken", "jersey city", "newark", "princeton",
            "parsippany", "morristown", "summit", "short hills", "montclair",
            "edison", "iselin", "basking ridge", "bridgewater", "white plains",
            "stamford",  # just over CT border, common NJ/NY commuter hub
        })
        is_hybrid_njny = "hybrid" in loc_clean and in_nj_or_ny

        # Also allow plain NJ/NY listings with no explicit remote/hybrid label
        is_njny_onsite = in_nj_or_ny and not any(kw in loc_clean for kw in {
            "chicago", "seattle", "austin", "boston", "denver", "atlanta",
            "dallas", "houston", "phoenix", "los angeles", "san francisco",
            "washington", "miami", "minneapolis", "portland", "detroit",
        })

        if not (is_remote or is_hybrid_njny or is_njny_onsite):
            continue

        # Hard exclude non-NJ/NY locations even if they sneak through above
        exclude_locations = {
            "seattle", ", wa", "chicago", ", il", "austin", ", tx",
            "boston", ", ma", "denver", ", co", "atlanta", ", ga",
            "dallas", "houston", "phoenix", ", az", "los angeles",
            "san francisco", ", ca", "washington, dc", "miami", ", fl",
            "minneapolis", ", mn", "portland", ", or", "detroit", ", mi",
        }
        if not is_remote and any(kw in loc_clean for kw in exclude_locations):
            continue

        # Seniority check
        if not any(kw in title for kw in seniority_keywords):
            continue
        if any(kw in title for kw in exclude_seniority):
            continue

        # Soft exclude non-tech industries
        combined = f"{company} {desc}"
        if any(kw in combined for kw in exclude_keywords):
            continue

        filtered.append(job)

    return filtered


# ─── Resume-Based Fit Scoring ──────────────────────────────────────────────────

# Nicole's background distilled into weighted signal keywords
NICOLE_PROFILE = {
    "core_strengths": [
        "operations", "product operations", "product management", "program management",
        "strategy", "cross-functional", "go-to-market", "gtm", "saas", "marketplace",
        "series a", "series b", "startup", "high-growth", "scale", "scaling",
        "roadmap", "sprint", "agile", "okr", "kpi", "analytics", "bi",
        "salesforce", "crm", "onboarding", "customer success", "retention",
        "net revenue retention", "nrr", "process design", "workflow", "automation",
        "stakeholder", "team leadership", "direct reports",
    ],
    "industries": [
        "tech", "technology", "software", "platform", "fintech", "edtech",
        "marketplace", "saas", "b2b", "startup",
    ],
    "fit_notes_map": {
        # Title keyword → why Nicole is a fit
        "product manager": "Nicole's 10+ years managing sprints, roadmaps, and cross-functional delivery at The Unquantifiable and Pinata align closely with this role.",
        "product operations": "Nicole built product operations from the ground up at Pinata — owning CRM, analytics, OKRs, and customer lifecycle — making her a natural fit.",
        "program manager": "Nicole's track record coordinating multi-phase initiatives across engineering, design, and external stakeholders maps directly to program management.",
        "operations manager": "Nicole scaled operations teams and systems at ClassPass (5,000+ partners) and Pinata (SaaS transition, Series A), demonstrating exactly the scope this role requires.",
        "strategy and operations": "Nicole's blend of strategic ownership (GTM, monetization, SaaS transition) and hands-on operations execution is a strong match for strategy & ops roles.",
        "business operations": "Nicole's experience building CRM infrastructure, BI platforms, and cross-functional workflows at high-growth startups is directly relevant here.",
        "director of operations": "Having led operations at both a scaling marketplace (ClassPass) and a SaaS startup (Pinata), Nicole is ready to step into a Director-level operations role.",
        "director of product": "Nicole's product management experience — from requirement definition to roadmap planning across 7 clients — positions her well for a Director of Product role.",
        "technical program manager": "Nicole's coordination of engineering, design, and business stakeholders across complex multi-phase initiatives reflects core TPM skills.",
    }
}


def score_fit(job: dict) -> tuple[int, str]:
    """
    Score how well the job matches Nicole's profile.
    Returns (score 0-100, fit_note string).
    """
    title = job["title"].lower()
    desc = job["description"].lower()
    combined = f"{title} {desc}"

    score = 0

    # Title match against core strengths
    for kw in NICOLE_PROFILE["core_strengths"]:
        if kw in combined:
            score += 3

    # Industry match
    for kw in NICOLE_PROFILE["industries"]:
        if kw in combined:
            score += 4

    # Cap at 100
    score = min(score, 100)

    # Generate fit note from title keyword mapping
    fit_note = ""
    for title_kw, note in NICOLE_PROFILE["fit_notes_map"].items():
        if title_kw in title:
            fit_note = note
            break

    # Generic fallback fit note
    if not fit_note and score >= 40:
        fit_note = "Nicole's operations and product management background at high-growth tech startups is a strong match for this role."
    elif not fit_note:
        fit_note = "Relevant to Nicole's cross-functional operations and product experience."

    return score, fit_note


def is_fractional(job: dict) -> bool:
    """Detect fractional / contract / part-time opportunities."""
    signals = ["fractional", "contract", "part-time", "part time", "freelance",
               "interim", "consulting", "consultant", "gig", "hourly", "1099"]
    combined = f"{job['title']} {job['description']} {job.get('source','')}".lower()
    return any(s in combined for s in signals)


def tag_and_score_jobs(jobs: list[dict]) -> list[dict]:
    """Tag each job as fractional or full-time, add fit score and fit note."""
    for job in jobs:
        job["is_fractional"] = is_fractional(job)
        job["fit_score"], job["fit_note"] = score_fit(job)
    return jobs


# ─── Email Composition ─────────────────────────────────────────────────────────

JOB_CARD_TEMPLATE = """
<div style="background:#ffffff;border:1px solid {border_color};border-radius:14px;
            padding:22px 24px;margin-bottom:20px;box-shadow:0 2px 6px rgba(0,0,0,0.06);">

  <!-- Top row: title + source badge -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;
              flex-wrap:wrap;gap:8px;margin-bottom:6px;">
    <h3 style="margin:0;font-size:17px;color:#1a202c;font-weight:800;line-height:1.3;
               flex:1;">{title}</h3>
    <span style="background:{badge_bg};color:{badge_color};padding:3px 10px;border-radius:20px;
                 font-size:11px;font-weight:700;letter-spacing:0.5px;white-space:nowrap;
                 text-transform:uppercase;">{source}</span>
  </div>

  <!-- Company name -->
  <a href="{company_website}" target="_blank"
     style="font-size:14px;color:#4f46e5;font-weight:700;text-decoration:none;
            display:inline-block;margin-bottom:14px;">{company} ↗</a>

  <!-- Bullet point details -->
  <table style="width:100%;border-collapse:collapse;margin-bottom:14px;">
    <tr>
      <td style="padding:4px 0;font-size:13px;color:#4a5568;width:50%;vertical-align:top;">
        📍 <strong>Location:</strong> {location}
      </td>
      <td style="padding:4px 0;font-size:13px;color:{salary_color};width:50%;vertical-align:top;">
        💰 <strong>Salary:</strong> {salary}
      </td>
    </tr>
    <tr>
      <td style="padding:4px 0;font-size:13px;color:#4a5568;vertical-align:top;">
        📅 <strong>Posted:</strong> {posted}
      </td>
      <td style="padding:4px 0;font-size:13px;color:#4a5568;vertical-align:top;">
        🏷 <strong>Type:</strong> {job_type}
      </td>
    </tr>
  </table>

  <!-- Divider -->
  <div style="border-top:1px solid #f0f0f0;margin-bottom:12px;"></div>

  <!-- Description -->
  <p style="margin:0 0 12px 0;font-size:13px;color:#555e6e;line-height:1.65;">
    {description}
  </p>

  <!-- Fit note -->
  {fit_note_html}

  <!-- Apply button -->
  <a href="{apply_link}" target="_blank"
     style="display:inline-block;margin-top:14px;background:#4f46e5;color:#ffffff;
            padding:10px 22px;border-radius:8px;text-decoration:none;font-size:13px;
            font-weight:700;letter-spacing:0.3px;">
    Apply Now →
  </a>

</div>
"""

FIT_NOTE_HTML = """
<div style="background:#f0fdf4;border-left:4px solid #22c55e;border-radius:0 8px 8px 0;
            padding:10px 14px;margin-bottom:4px;">
  <p style="margin:0;font-size:12px;color:#166534;line-height:1.6;">
    <strong>✨ Why you're a fit:</strong> {fit_note}
  </p>
</div>
"""

SECTION_HEADER_TEMPLATE = """
<div style="margin:36px 0 18px 0;">
  <div style="background:{bg};border-radius:12px;padding:18px 22px;
              border-left:5px solid {accent};">
    <h2 style="margin:0 0 5px 0;font-size:19px;font-weight:800;color:{color};">
      {icon} {title}
    </h2>
    <p style="margin:0;font-size:13px;color:{subcolor};">{subtitle}</p>
  </div>
</div>
"""


def get_source_badge_style(source: str) -> tuple[str, str]:
    """Return (bg_color, text_color) for the source badge."""
    styles = {
        "LinkedIn":    ("#dbeafe", "#1e40af"),
        "Google Jobs": ("#ede9fe", "#5b21b6"),
        "Remotive":    ("#dcfce7", "#166534"),
        "The Muse":    ("#fef3c7", "#92400e"),
        "Jobicy":      ("#fce7f3", "#9d174d"),
    }
    return styles.get(source, ("#f1f5f9", "#475569"))


def render_cards(jobs: list[dict], top_fit_threshold: int = 55) -> str:
    """Render a list of job dicts into HTML cards."""
    if not jobs:
        return """
        <div style="text-align:center;padding:32px 24px;background:#f8fafc;
                    border-radius:12px;border:1px dashed #cbd5e1;">
          <p style="margin:0;color:#94a3b8;font-size:14px;">
            No matching roles found this week — check back next Monday!
          </p>
        </div>"""

    html = ""
    for job in jobs:
        salary_has_data = job["salary"] not in ("Not listed", "", None, "N/A")
        is_top = job.get("fit_score", 0) >= top_fit_threshold
        badge_bg, badge_color = get_source_badge_style(job["source"])
        fit_note_html = FIT_NOTE_HTML.format(fit_note=job.get("fit_note", "")) if job.get("fit_note") else ""
        job_type = "Fractional / Contract" if job.get("is_fractional") else "Full-Time"

        html += JOB_CARD_TEMPLATE.format(
            title=job["title"],
            company=job["company"],
            company_website=job["company_website"],
            location=job["location"],
            salary=job["salary"] if salary_has_data else "Not listed",
            salary_color="#166534" if salary_has_data else "#94a3b8",
            posted=job["posted"],
            job_type=job_type,
            description=job["description"],
            apply_link=job["apply_link"],
            source=job["source"],
            badge_bg=badge_bg,
            badge_color=badge_color,
            fit_note_html=fit_note_html,
            border_color="#86efac" if is_top else "#e2e8f0",
        )
    return html


def build_email_html(fulltime_jobs: list[dict], fractional_jobs: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    # Sort: top fit first, then salary-disclosed
    fulltime_jobs.sort(key=lambda j: (-j.get("fit_score", 0),
                                       j["salary"] in ("Not listed", "", None, "N/A")))
    fractional_jobs.sort(key=lambda j: (-j.get("fit_score", 0),
                                         j["salary"] in ("Not listed", "", None, "N/A")))

    ft_section = SECTION_HEADER_TEMPLATE.format(
        bg="#eef2ff", accent="#4f46e5", color="#3730a3", subcolor="#6366f1",
        icon="💼", title="Full-Time Roles",
        subtitle=f"{len(fulltime_jobs)} role{'s' if len(fulltime_jobs) != 1 else ''} · Remote or Hybrid (NJ / NY) · PM, Operations & Strategy"
    ) + render_cards(fulltime_jobs)

    frac_section = SECTION_HEADER_TEMPLATE.format(
        bg="#fdf4ff", accent="#a855f7", color="#7e22ce", subcolor="#a855f7",
        icon="⚡", title="Fractional & Contract Opportunities",
        subtitle=f"{len(fractional_jobs)} opportunit{'ies' if len(fractional_jobs) != 1 else 'y'} · Flexible engagements · PM, Operations & Strategy"
    ) + render_cards(fractional_jobs)

    top_count = sum(1 for j in fulltime_jobs + fractional_jobs if j.get("fit_score", 0) >= 55)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nicole's Weekly Job Digest</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#4338ca 0%,#7c3aed 100%);
              padding:40px 24px 32px;text-align:center;">
    <p style="margin:0 0 8px 0;color:#c7d2fe;font-size:12px;letter-spacing:3px;
              text-transform:uppercase;font-weight:600;">Weekly Job Digest</p>
    <h1 style="margin:0 0 10px 0;color:#ffffff;font-size:30px;font-weight:800;line-height:1.2;">
      Hey Nicole, here are your matches 👋
    </h1>
    <p style="margin:0;color:#a5b4fc;font-size:15px;">{today}</p>
  </div>

  <!-- Stats bar -->
  <div style="background:#ffffff;border-bottom:2px solid #e2e8f0;padding:0;">
    <div style="display:flex;max-width:680px;margin:0 auto;">
      <div style="flex:1;text-align:center;padding:16px 8px;border-right:1px solid #f0f0f0;">
        <p style="margin:0;font-size:22px;font-weight:800;color:#4f46e5;">{len(fulltime_jobs)}</p>
        <p style="margin:0;font-size:11px;color:#94a3b8;text-transform:uppercase;
                  letter-spacing:1px;font-weight:600;">Full-Time</p>
      </div>
      <div style="flex:1;text-align:center;padding:16px 8px;border-right:1px solid #f0f0f0;">
        <p style="margin:0;font-size:22px;font-weight:800;color:#7c3aed;">{len(fractional_jobs)}</p>
        <p style="margin:0;font-size:11px;color:#94a3b8;text-transform:uppercase;
                  letter-spacing:1px;font-weight:600;">Fractional</p>
      </div>
      <div style="flex:1;text-align:center;padding:16px 8px;">
        <p style="margin:0;font-size:22px;font-weight:800;color:#16a34a;">{top_count}</p>
        <p style="margin:0;font-size:11px;color:#94a3b8;text-transform:uppercase;
                  letter-spacing:1px;font-weight:600;">Top Fits ✨</p>
      </div>
    </div>
  </div>

  <!-- Legend -->
  <div style="background:#fffbeb;border-bottom:1px solid #fde68a;padding:10px 24px;
              text-align:center;">
    <p style="margin:0;font-size:12px;color:#92400e;">
      🟢 <strong>Green border</strong> = top fit based on your resume &nbsp;·&nbsp;
      ✨ <strong>Why you're a fit</strong> notes appear on matching roles
    </p>
  </div>

  <!-- Content -->
  <div style="max-width:680px;margin:0 auto;padding:8px 16px 40px;">
    {ft_section}
    {frac_section}
  </div>

  <!-- Footer -->
  <div style="background:#1e1b4b;text-align:center;padding:28px 24px;">
    <p style="margin:0 0 6px 0;color:#a5b4fc;font-size:13px;font-weight:600;">
      Nicole's Weekly Job Digest
    </p>
    <p style="margin:0;color:#6366f1;font-size:12px;">
      Sent every Monday at 10 AM · Jobs posted in the last 30 days · Always verify before applying
    </p>
  </div>

</body>
</html>"""


# ─── Send Email ────────────────────────────────────────────────────────────────

def send_email(service, html_body: str, fulltime_count: int, fractional_count: int):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"🔍 Your Job Digest — {fulltime_count} Full-Time + {fractional_count} Fractional Roles ({today})"

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SENDER_EMAIL
    message["To"] = RECIPIENT_EMAIL
    message.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()
    print(f"✅ Email sent: '{subject}'")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🔍 Starting job search...")

    jobs = collect_all_jobs()
    print(f"   Raw jobs collected: {len(jobs)}")

    jobs = tag_and_score_jobs(jobs)
    jobs = filter_jobs(jobs)
    print(f"   Jobs after filtering: {len(jobs)}")

    fulltime_jobs  = [j for j in jobs if not j.get("is_fractional")]
    fractional_jobs = [j for j in jobs if j.get("is_fractional")]

    print(f"   Full-time: {len(fulltime_jobs)} · Fractional: {len(fractional_jobs)}")

    html = build_email_html(fulltime_jobs, fractional_jobs)

    print("📧 Sending email via Gmail API...")
    service = get_gmail_service()
    send_email(service, html, len(fulltime_jobs), len(fractional_jobs))
    print("✅ Done.")


if __name__ == "__main__":
    main()
