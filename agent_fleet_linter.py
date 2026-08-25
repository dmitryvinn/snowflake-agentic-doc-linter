#!/usr/bin/env python3
"""
Snowflake Agentic DX: Document-Type Fleet Scanner
Recursively audits Snowflake documentation endpoints parsed from /llms.txt to evaluate LLM context readiness.
"""

import sys
import re
import urllib.request
import urllib.error
import time
from urllib.parse import urljoin, urlparse

try:
    import tiktoken
except ImportError:
    print("❌ Missing dependency. Please run: pip install tiktoken")
    sys.exit(1)

# CLI Argument Parsing
MAX_FILES = 5
if len(sys.argv) > 1:
    try:
        MAX_FILES = int(sys.argv[1])
    except ValueError:
        print(f"❌ Invalid argument. Usage: python3 {sys.argv[0]} [max_files]")
        sys.exit(1)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
}
ALLOWED_DOMAIN = "docs.snowflake.com"
ROOT_URL = "https://docs.snowflake.com/llms.txt"


def fetch_with_retry(url, max_retries=2, base_delay=1.0):
    """Fetches URL with exponential backoff on transient errors; fast-fails on 404/403."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=6) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 400, 410):
                raise e
            if e.code in (503, 429, 500, 502, 504) and attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise e
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(base_delay)
                continue
            raise e
    return None


def normalize_doc_url(base_url, raw_href):
    """Safely constructs valid Snowflake .md URLs, ignoring template/placeholder links."""
    try:
        clean_href = raw_href.split('#')[0].split('?')[0].strip()
        if not clean_href or clean_href.startswith(('mailto:', 'javascript:', 'data:')):
            return None

        # Ignore template placeholders (e.g., https://[account].snowflake...)
        if '[' in clean_href or ']' in clean_href or '<' in clean_href or '>' in clean_href:
            return None

        if clean_href.startswith(('http://', 'https://')):
            target = clean_href
        else:
            if clean_href.startswith('/'):
                target = f"https://{ALLOWED_DOMAIN}{clean_href}"
            else:
                target = urljoin(base_url, clean_href)

        parsed = urlparse(target)
        if parsed.netloc != ALLOWED_DOMAIN:
            return None

        path = parsed.path
        if path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.css', '.js', '.svg', '/llms.txt', '/llms-full.txt')):
            return None

        if path.endswith('.md'):
            return target
        
        clean_path = path.rstrip('/')
        if clean_path.endswith('.html'):
            clean_path = clean_path[:-5]

        if not clean_path.startswith('/en/'):
            clean_path = '/en' + clean_path

        return f"https://{ALLOWED_DOMAIN}{clean_path}.md"
    except (ValueError, Exception):
        return None


def extract_child_md_links(content, base_url):
    """Extracts Snowflake doc links from page content safely."""
    found_urls = set()
    markdown_hrefs = re.findall(r'\[.*?\]\(([^\s\)\"]+)\)', content)
    bare_urls = re.findall(r'(https?://[^\s\)\"\'>]+)', content)
    
    for raw in set(markdown_hrefs + bare_urls):
        url = normalize_doc_url(base_url, raw)
        if url:
            found_urls.add(url)
                
    return found_urls


def classify_and_lint(doc_name, content, tokens):
    """Evaluates document against Document-Type Taxonomy Quality Gates."""
    doc_lower = doc_name.lower()
    
    has_code = bool(re.search(r'```.*?```', content, re.DOTALL))
    has_rbac = bool(re.search(r'(RBAC|GRANT|ROLE|ACCOUNTADMIN|SYSADMIN|CORTEX_USER)', content, re.IGNORECASE))
    has_sdk  = bool(re.search(r'(version|>=|==|pip install)', content, re.IGNORECASE))
    has_links = bool(re.search(r'\[.*?\]\(.*?\)', content)) or bool(re.search(r'https?://', content))
    
    overview_keywords = [
        'overview', 'index', 'appendices', 'getting-started', 'resources', 
        'user-guide', 'learn', 'tutorial', 'guide', 'admin', 'manage', 'management',
        'intro', 'introduction', 'release', 'releases', 'region', 'regions',
        'compliance', 'security', 'architecture'
    ]
    
    reference_keywords = [
        'data-types', 'reference', 'concepts', 'classes', 'functions', 
        'sql-reference', 'commands', 'syntax'
    ]

    # 1. OVERVIEW DOCS (Index & Routing Hubs)
    if any(k in doc_lower for k in overview_keywords):
        doc_type = "OVERVIEW"
        if "index" in doc_lower:
            passed = (tokens < 1500) and has_links
            reason = "Lean Root Router" if passed else "Needs Split (Index Bloat)"
        else:
            passed = (tokens < 2500) and has_links
            reason = "Lean & Link-Rich" if passed else "Context Bloat (>2.5k tokens)"
        
    # 2. REFERENCE DOCS (Data Types, Syntax Lookups)
    elif any(k in doc_lower for k in reference_keywords):
        doc_type = "REFERENCE"
        passed = (tokens < 4000)
        reason = "Concise Lookup" if passed else "Bloated Reference (>4k tokens)"
        
    # 3. IMPLEMENTATION DOCS (Actionable Execution Guides)
    else:
        doc_type = "IMPLEMENTATION"
        passed = has_code and has_rbac and has_sdk
        missing = []
        if not has_code: missing.append("No Code")
        if not has_rbac: missing.append("No RBAC")
        if not has_sdk: missing.append("No SDK bounds")
        reason = "Agent-Ready" if passed else f"Gaps: {', '.join(missing)}"

    status = "🟢 PASS" if passed else "🔴 FAIL"
    return doc_type, status, reason


def run_recursive_fleet_linter():
    print("\n============================================================")
    print("❄️  DEVREL AGENTIC DX: RECURSIVE FLEET SCANNER")
    print(f"Target Root: {ROOT_URL} | Limit: {MAX_FILES} file(s)")
    print("============================================================\n")

    try:
        llms_content = fetch_with_retry(ROOT_URL, max_retries=3)
    except Exception as e:
        print(f"❌ Failed to fetch llms.txt index: {e}")
        return

    root_links = extract_child_md_links(llms_content, base_url=ROOT_URL)
    if not root_links:
        print("❌ No valid markdown endpoints discovered in root llms.txt.")
        return

    queue = [(url, 1) for url in sorted(list(root_links))]
    visited = set()
    results = []
    encoder = tiktoken.get_encoding("cl100k_base")

    print(f"[+] Discovered {len(queue)} Depth 1 endpoints. Traversing graph recursively...\n")

    while queue and len(results) < MAX_FILES:
        url, depth = queue.pop(0)
        
        if url in visited:
            continue
        visited.add(url)

        doc_name = url.split('/')[-1].replace('.md', '')
        if not doc_name or doc_name.lower() in ['llms', 'llms-full']:
            continue

        try:
            content = fetch_with_retry(url, max_retries=2, base_delay=1.0)
        except Exception:
            continue

        tokens = len(encoder.encode(content))
        doc_type, status, reason = classify_and_lint(doc_name, content, tokens)

        count_str = f"[{len(results)+1}/{MAX_FILES}]"
        depth_str = f"[Depth {depth}]"
        doc_disp = doc_name[:32]
        reason_disp = reason[:38]
        
        print(f"{count_str:<9} {depth_str:<10} Auditing Document: {doc_disp:<32} -> {status} [{doc_type:<14} | {tokens:>6,} tokens | {reason_disp:<38}]")

        results.append({
            "name": doc_name[:32],
            "depth": depth,
            "doc_type": doc_type,
            "tokens": tokens,
            "status": status,
            "reason": reason[:38],
            "url": url
        })

        child_links = extract_child_md_links(content, base_url=url)
        for child_url in child_links:
            if child_url not in visited:
                queue.append((child_url, depth + 1))

        time.sleep(0.2)

    # Dashboard Output
    print("\n\n📊 FINAL FLEET READINESS DASHBOARD (RECURSIVE GRAPH AUDIT)")
    print("-" * 175)
    print(f"{'DOCUMENT':<32} | {'DEPTH':<5} | {'DOC TYPE':<14} | {'TOKENS':<8} | {'STATUS':<7} | {'EVALUATION REASON':<38} | {'URL'}")
    print("-" * 175)
    
    pass_count = 0
    for r in results:
        if "PASS" in r['status']: pass_count += 1
        print(f"{r['name']:<32} | {r['depth']:<5} | {r['doc_type']:<14} | {r['tokens']:<8,} | {r['status']:<7} | {r['reason']:<38} | {r['url']}")
        
    print("-" * 175)
    score = (pass_count / len(results) * 100) if results else 0
    print(f"TAXONOMY AGENT READINESS INDEX: {score:.1f}%\n")


if __name__ == "__main__":
    run_recursive_fleet_linter()