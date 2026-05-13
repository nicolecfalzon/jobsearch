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
    "VP of Operations",
    "Head of Operations",
    "Product Manager",
    "Senior Product Manager",
    "Director of Product Management",
    "VP of Product",
    "Head of Product",
    "Product Operations Manager",
    "Senior Product Operations Manager",
    "Director of Product Operations",
    "Technical Program Manager",
    "Senior Technical Program Manager",
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
    Extract a short summary: up to 2 sentences about the company,
    then up to 2 sentences about the role itself.
    """
    if not description:
        return "No description available."

    # Strip HTML tags if present
    import re
    clean = re.sub(r"<[^>]+>", " ", description)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Split into sentences (naive but effective)
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    company_sentences = []
    role_sentences = []

    company_lower = company_name.lower() if company_name else ""
    role_keywords = {"role", "you will", "you'll", "responsibilities", "looking for",
                     "candidate", "position", "opportunity", "join", "ideal"}
    company_keywords = {"we are", "we're", "our company", "founded", "platform",
                        "mission", "we build", "we help", "we provide", company_lower}

    for s in sentences:
        s_lower = s.lower()
        if any(kw in s_lower for kw in company_keywords) and len(company_sentences) < 2:
            company_sentences.append(s)
        elif any(kw in s_lower for kw in role_keywords) and len(role_sentences) < 2:
            role_sentences.append(s)
        if len(company_sentences) >= 2 and len(role_sentences) >= 2:
            break

    # If we couldn't cleanly split, just take the first 3 sentences total
    if not company_sentences and not role_sentences:
        return " ".join(sentences[:3])

    # Pad with sequential sentences if one bucket is empty
    if not company_sentences and sentences:
        company_sentences = [sentences[0]]
    if not role_sentences and len(sentences) > 1:
        role_sentences = [sentences[1]]

    return " ".join(company_sentences + role_sentences)


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
            ("VP operations tech startup remote", ""),
            ("product operations manager senior tech remote hybrid", ""),
            # Product roles
            ("senior product manager tech company remote", ""),
            ("director of product management remote OR hybrid NY NJ", ""),
            ("VP of product tech remote", ""),
            ("head of product remote hybrid New York", ""),
            # Blend roles
            ("product operations director remote hybrid NJ NY", ""),
            ("technical program manager senior remote hybrid", ""),
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
        "VP product",
        "head of product",
        "technical program manager",
    ]
    for kw in remotive_searches:
        raw_jobs = search_remotive(kw)
        for r in raw_jobs:
            all_jobs.append(normalize_remotive_job(r))
        time.sleep(0.5)

    return deduplicate(all_jobs)


def filter_jobs(jobs: list[dict]) -> list[dict]:
    """
    Keep jobs that:
    - Are remote, OR hybrid in NJ/NY
    - Match target seniority
    - Are from tech companies (best-effort keyword filter)
    """
    location_keywords = {
        "remote", "anywhere", "hybrid", "new york", "new jersey", "ny", "nj", "nyc"
    }
    seniority_keywords = {
        "senior", "sr.", "sr ", "director", "vp", "vice president",
        "head of", "lead", "principal", "manager"
    }
    # Exclude clearly non-tech industries
    exclude_keywords = {
        "retail", "restaurant", "food service", "hospitality", "healthcare clinic",
        "manufacturing", "warehouse", "logistics driver"
    }

    filtered = []
    for job in jobs:
        loc = job["location"].lower()
        title = job["title"].lower()
        desc = job["description"].lower()
        company = job["company"].lower()

        # Location check
        location_ok = any(kw in loc for kw in location_keywords)
        if not location_ok:
            continue

        # If hybrid, must be NJ or NY
        if "hybrid" in loc and not any(kw in loc for kw in {"new york", "new jersey", "ny", "nj", "nyc"}):
            continue

        # Seniority check
        seniority_ok = any(kw in title for kw in seniority_keywords)
        if not seniority_ok:
            continue

        # Soft exclude non-tech
        combined = f"{company} {desc}"
        if any(kw in combined for kw in exclude_keywords):
            continue

        filtered.append(job)

    return filtered


# ─── Email Composition ─────────────────────────────────────────────────────────

JOB_CARD_TEMPLATE = """
<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;
            padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:8px;">
    <div>
      <h3 style="margin:0 0 4px 0;font-size:18px;color:#1a202c;font-weight:700;">{title}</h3>
      <a href="{company_website}" style="margin:0;font-size:15px;color:#4f46e5;font-weight:600;text-decoration:none;">{company} ↗</a>
    </div>
    <span style="background:#ebf8ff;color:#2b6cb0;padding:4px 12px;border-radius:20px;
                 font-size:13px;font-weight:600;white-space:nowrap;">{source}</span>
  </div>

  <div style="display:flex;flex-wrap:wrap;gap:12px;margin:16px 0;">
    <span style="background:#f7fafc;border:1px solid #e2e8f0;border-radius:6px;
                 padding:4px 10px;font-size:13px;color:#4a5568;">
      📍 {location}
    </span>
    <span style="background:{salary_bg};border:1px solid #e2e8f0;border-radius:6px;
                 padding:4px 10px;font-size:13px;color:{salary_color};font-weight:600;">
      💰 {salary}
    </span>
    <span style="background:#f7fafc;border:1px solid #e2e8f0;border-radius:6px;
                 padding:4px 10px;font-size:13px;color:#718096;">
      📅 Posted: {posted}
    </span>
  </div>

  <p style="margin:0 0 16px 0;font-size:14px;color:#4a5568;line-height:1.6;">{description}</p>

  <div style="display:flex;gap:12px;flex-wrap:wrap;">
    <a href="{apply_link}" style="background:#4f46e5;color:#ffffff;padding:10px 20px;
       border-radius:8px;text-decoration:none;font-size:14px;font-weight:600;">
      Apply Now →
    </a>
    <a href="{hiring_contact}" style="background:#f7fafc;color:#4f46e5;padding:10px 20px;
       border-radius:8px;text-decoration:none;font-size:14px;font-weight:600;
       border:1px solid #e2e8f0;">
      🔗 Find Hiring Manager on LinkedIn
    </a>
  </div>
</div>
"""


def build_email_html(jobs: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    count = len(jobs)

    if not jobs:
        body_content = """
        <div style="text-align:center;padding:48px 24px;color:#718096;">
          <p style="font-size:18px;">No new matching jobs found this week.</p>
          <p>Check back next Monday — new roles are posted daily!</p>
        </div>
        """
    else:
        cards = ""
        for job in jobs:
            salary_has_data = job["salary"] not in ("Not listed", "", None, "N/A")
            cards += JOB_CARD_TEMPLATE.format(
                title=job["title"],
                company=job["company"],
                company_website=job["company_website"],
                location=job["location"],
                salary=job["salary"],
                salary_bg="#f0fff4" if salary_has_data else "#f7fafc",
                salary_color="#276749" if salary_has_data else "#718096",
                posted=job["posted"],
                description=job["description"],
                apply_link=job["apply_link"],
                hiring_contact=job["hiring_contact"],
                source=job["source"],
            )
        body_content = cards

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your Weekly Job Digest</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);padding:40px 24px;text-align:center;">
    <p style="margin:0 0 8px 0;color:#c7d2fe;font-size:14px;letter-spacing:2px;
              text-transform:uppercase;font-weight:600;">Weekly Job Digest</p>
    <h1 style="margin:0 0 8px 0;color:#ffffff;font-size:32px;font-weight:800;">
      Your Job Matches 🚀
    </h1>
    <p style="margin:0;color:#a5b4fc;font-size:16px;">{today}</p>
  </div>

  <!-- Summary Bar -->
  <div style="background:#ffffff;border-bottom:1px solid #e2e8f0;padding:16px 24px;text-align:center;">
    <p style="margin:0;font-size:15px;color:#4a5568;">
      Found <strong style="color:#4f46e5;">{count} matching role{"s" if count != 1 else ""}</strong>
      in Operations &amp; Product Management &nbsp;·&nbsp;
      Remote / Hybrid (NJ &amp; NY) &nbsp;·&nbsp;
      Mid-level through VP+
    </p>
  </div>

  <!-- Main Content -->
  <div style="max-width:700px;margin:32px auto;padding:0 16px;">
    {body_content}
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:32px 24px;color:#a0aec0;font-size:13px;">
    <p style="margin:0 0 4px 0;">This digest is generated every Monday at 10 AM.</p>
    <p style="margin:0;">Jobs shown were posted in the past 30 days.
       Always verify listing details before applying.</p>
  </div>

</body>
</html>
"""


# ─── Send Email ────────────────────────────────────────────────────────────────

def send_email(service, html_body: str, job_count: int):
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"🔍 Your Weekly Job Digest — {job_count} New Role{'s' if job_count != 1 else ''} ({today})"

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

    jobs = filter_jobs(jobs)
    print(f"   Jobs after filtering: {len(jobs)}")

    # Sort: salary-disclosed first, then alphabetical by company
    jobs.sort(key=lambda j: (j["salary"] in ("Not listed", "", None, "N/A"), j["company"].lower()))

    html = build_email_html(jobs)

    print("📧 Sending email via Gmail API...")
    service = get_gmail_service()
    send_email(service, html, len(jobs))
    print("✅ Done.")


if __name__ == "__main__":
    main()
