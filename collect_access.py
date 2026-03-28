"""
collect_access.py
Fetches GitHub traffic data for all repos owned by MuscleLove-777
and accumulates historical data in access_data.json.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

OWNER = "MuscleLove-777"
API_BASE = "https://api.github.com"
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_data.json")


def get_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable is not set.")
        sys.exit(1)
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def api_get(url, headers):
    """Make a GET request with rate-limit handling."""
    resp = requests.get(url, headers=headers, timeout=30)

    # Handle rate limiting
    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        reset_ts = resp.headers.get("X-RateLimit-Reset")
        if remaining == "0" and reset_ts:
            wait = max(int(reset_ts) - int(time.time()), 1) + 5
            print(f"  Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            resp = requests.get(url, headers=headers, timeout=30)

    return resp


def fetch_all_repos(headers):
    """Fetch all repos for the owner (handles pagination)."""
    repos = []
    page = 1
    while True:
        url = f"{API_BASE}/users/{OWNER}/repos?per_page=100&page={page}"
        resp = api_get(url, headers)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
        time.sleep(0.5)
    return repos


def fetch_traffic_views(repo_name, headers):
    """Fetch 14-day page view traffic for a repo."""
    url = f"{API_BASE}/repos/{OWNER}/{repo_name}/traffic/views"
    resp = api_get(url, headers)
    if resp.status_code == 403:
        return None  # Traffic API not available for this repo
    if resp.status_code != 200:
        return None
    return resp.json()


def fetch_traffic_clones(repo_name, headers):
    """Fetch 14-day clone traffic for a repo."""
    url = f"{API_BASE}/repos/{OWNER}/{repo_name}/traffic/clones"
    resp = api_get(url, headers)
    if resp.status_code == 403:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def load_existing_data():
    """Load existing access_data.json if it exists."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": None, "repos": {}, "summary": {}}


def merge_daily_data(existing_daily, new_views):
    """Merge new daily view data with existing, avoiding duplicates by date."""
    existing_by_date = {entry["date"]: entry for entry in existing_daily}

    for view in new_views:
        # GitHub returns timestamps like "2026-03-27T00:00:00Z"
        date_str = view["timestamp"][:10]
        entry = {
            "date": date_str,
            "views": view["count"],
            "uniques": view["uniques"],
        }
        existing_by_date[date_str] = entry

    merged = sorted(existing_by_date.values(), key=lambda x: x["date"])
    return merged


def save_data(data):
    """Save data to access_data.json."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    headers = get_headers()
    print(f"Fetching repos for {OWNER}...")

    repos = fetch_all_repos(headers)
    print(f"Found {len(repos)} repositories.")

    data = load_existing_data()

    processed = 0
    skipped = 0

    for repo in repos:
        repo_name = repo["name"]
        has_pages = repo.get("has_pages", False)
        homepage = repo.get("homepage", "") or ""

        # Determine GitHub Pages URL
        if has_pages and not homepage:
            homepage = f"https://{OWNER.lower()}.github.io/{repo_name}"

        print(f"  [{processed + 1}/{len(repos)}] {repo_name}", end="")

        # Fetch traffic data
        views_data = fetch_traffic_views(repo_name, headers)
        clones_data = fetch_traffic_clones(repo_name, headers)

        if views_data is None:
            print(" - skipped (no traffic access)")
            skipped += 1
            processed += 1
            time.sleep(0.3)
            continue

        # Get or create repo entry
        repo_entry = data["repos"].get(repo_name, {
            "total_views": 0,
            "total_uniques": 0,
            "total_clones": 0,
            "total_clone_uniques": 0,
            "is_github_pages": False,
            "homepage": "",
            "daily": [],
        })

        # Update metadata
        repo_entry["is_github_pages"] = has_pages
        if has_pages and homepage:
            repo_entry["homepage"] = homepage

        # Merge daily view data
        new_views = views_data.get("views", [])
        repo_entry["daily"] = merge_daily_data(repo_entry["daily"], new_views)

        # Recalculate totals from accumulated daily data
        repo_entry["total_views"] = sum(d["views"] for d in repo_entry["daily"])
        repo_entry["total_uniques"] = sum(d["uniques"] for d in repo_entry["daily"])

        # Clone totals (from the 14-day window, not accumulated per-day)
        if clones_data:
            repo_entry["total_clones"] = clones_data.get("count", 0)
            repo_entry["total_clone_uniques"] = clones_data.get("uniques", 0)

        data["repos"][repo_name] = repo_entry

        view_count = views_data.get("count", 0)
        print(f" - {view_count} views (14d)")

        processed += 1
        time.sleep(0.3)

    # Build summary
    all_repos = data["repos"]
    total_views = sum(r["total_views"] for r in all_repos.values())
    total_uniques = sum(r["total_uniques"] for r in all_repos.values())

    top_repos = sorted(
        [{"name": name, "views": info["total_views"]} for name, info in all_repos.items()],
        key=lambda x: x["views"],
        reverse=True,
    )[:10]

    data["summary"] = {
        "total_repos_tracked": len(all_repos),
        "total_views_all": total_views,
        "total_uniques_all": total_uniques,
        "top_repos": top_repos,
    }

    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    save_data(data)

    # Print summary
    print("\n" + "=" * 60)
    print("ACCESS TRACKER SUMMARY")
    print("=" * 60)
    print(f"Repos tracked:    {len(all_repos)}")
    print(f"Repos processed:  {processed} ({skipped} skipped)")
    print(f"Total views:      {total_views:,}")
    print(f"Total uniques:    {total_uniques:,}")
    print(f"\nTop 10 repos by views:")
    for i, r in enumerate(top_repos, 1):
        print(f"  {i:2d}. {r['name']:<40s} {r['views']:>6,} views")

    pages_repos = [n for n, info in all_repos.items() if info.get("is_github_pages")]
    print(f"\nGitHub Pages sites: {len(pages_repos)}")
    for name in pages_repos[:10]:
        url = all_repos[name].get("homepage", "")
        print(f"  - {name}: {url}")

    print(f"\nData saved to {DATA_FILE}")
    print(f"Last updated: {data['last_updated']}")


if __name__ == "__main__":
    main()
