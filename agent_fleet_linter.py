#!/usr/bin/env python3
"""Structural evaluation harness for Snowflake's agent-facing documentation graph.

This scanner produces review signals. It does not claim to prove end-to-end
agent correctness or developer-task success.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

try:
    import tiktoken
except ImportError:
    print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)


ALLOWED_DOMAIN = "docs.snowflake.com"
ROOT_URL = "https://docs.snowflake.com/llms.txt"
DEFAULT_MAX_FILES = 25
DEFAULT_TIMEOUT_SECONDS = 8.0

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "blue": "\033[34m",
}

HEADERS = {
    "User-Agent": (
        "Snowflake-Agentic-Doc-Scanner/0.2 "
        "(+https://github.com/dmitryvinn/snowflake-agentic-doc-linter)"
    ),
    "Accept": "text/markdown,text/plain,text/html;q=0.8,*/*;q=0.5",
}

THRESHOLDS = {
    "root_index_tokens": 1_500,
    "overview_tokens": 2_500,
    "reference_tokens": 6_000,
    "implementation_tokens": 8_000,
}

EXECUTABLE_FENCE_RE = re.compile(
    r"```(?:python|py|sql|bash|sh|shell|javascript|js|typescript|ts|java|scala|go|golang|csharp|cs|powershell|yaml|yml|json|toml)\s*\n.+?```",
    re.IGNORECASE | re.DOTALL,
)
PRIVILEGE_RE = re.compile(
    r"\b(?:RBAC|GRANT|REVOKE|CREATE\s+ROLE|USE\s+ROLE|least[ -]privilege|"
    r"access control|privilege(?:s)?)\b",
    re.IGNORECASE,
)
SECURITY_LINK_RE = re.compile(
    r"(?:security-access-control|access-control-overview|access-control-privileges)",
    re.IGNORECASE,
)
VERSION_BOUND_RE = re.compile(
    r"(?:pip\s+install|uv\s+add|poetry\s+add|npm\s+install|yarn\s+add)"
    r"[^\n`]*(?:==|>=|<=|~=|\^|@[~^]?\d)|"
    r"\b(?:requires?|minimum|supported|tested)\b[^\n]{0,60}"
    r"\b(?:SDK|driver|connector|version)\b[^\n]{0,30}\d+\.\d+",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^\)]+\)")
BARE_URL_RE = re.compile(r"https?://[^\s\)\"'>]+")


@dataclass(frozen=True)
class Signals:
    executable_code: bool
    privilege_guidance: bool
    version_bounds: bool
    outbound_links: bool
    heading_count: int


@dataclass
class AuditResult:
    name: str
    url: str
    depth: int
    doc_type: str
    tokens: int
    status: str
    findings: list[str]
    signals: Optional[Signals] = None
    error: Optional[str] = None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl Snowflake's public agent-facing documentation graph and "
            "produce transparent structural review signals."
        )
    )
    parser.add_argument(
        "legacy_max_files",
        nargs="?",
        type=int,
        help="Backward-compatible positional document limit.",
    )
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Audit URLs from a newline-delimited manifest instead of crawling llms.txt.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use the deterministic demo_manifest.txt bundled with this repository.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Retained for compatibility; text mode streams progress by default.",
    )
    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument(
        "--color",
        action="store_true",
        help="Force ANSI color output, including when stdout is redirected.",
    )
    color_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help="Exit nonzero when any REVIEW result is produced.",
    )
    args = parser.parse_args(argv)
    args.max_files = args.max_files or args.legacy_max_files or DEFAULT_MAX_FILES
    if args.max_files < 1:
        parser.error("--max-files must be positive")
    if args.demo and args.manifest:
        parser.error("--demo and --manifest are mutually exclusive")
    if args.demo:
        args.manifest = Path(__file__).with_name("demo_manifest.txt")
    return args


def color_enabled(args: argparse.Namespace) -> bool:
    return not args.no_color


def paint(value: str, color: str, enabled: bool, *, bold: bool = False) -> str:
    if not enabled:
        return value
    prefix = ANSI[color]
    if bold:
        prefix = ANSI["bold"] + prefix
    return f"{prefix}{value}{ANSI['reset']}"


def print_scan_header(args: argparse.Namespace, seed_count: int) -> None:
    enabled = color_enabled(args)
    target = min(seed_count, args.max_files) if args.manifest else args.max_files
    print()
    print(paint("=" * 60, "cyan", enabled))
    print(paint("❄️  DEVREL AGENTIC DX: RECURSIVE FLEET SCANNER", "cyan", enabled, bold=True))
    print(f"Target Root: {ROOT_URL} | Limit: {target} file(s)")
    print(paint("=" * 60, "cyan", enabled))
    print()
    if args.manifest:
        print(f"[+] Loaded {seed_count} explicit endpoints. Auditing line by line...")
    else:
        print(f"[+] Discovered {seed_count} Depth 1 endpoints. Traversing graph recursively...")
    print()


def result_reason(result: AuditResult) -> str:
    if result.error:
        return result.error
    if result.status == "SKIP":
        return "HTML-only surface"
    if result.status == "PASS":
        if result.doc_type == "OVERVIEW":
            return "Lean Root Router" if result.name.endswith("index") else "Lean & Link-Rich"
        if result.doc_type == "REFERENCE":
            return "Concise Lookup"
        return "Implementation signals present"

    joined = " ".join(result.findings).lower()
    if result.doc_type == "BROKEN_ROUTE":
        return "Broken route (404)"
    if "splitting" in joined or "routing" in joined:
        return "Review routing / index size"
    if "chunking the reference" in joined:
        return "Review reference chunking"
    gaps = []
    if "executable-language" in joined:
        gaps.append("Code")
    if "privilege guidance" in joined:
        gaps.append("Access")
    if "version bound" in joined:
        gaps.append("Version")
    if gaps:
        return "Gaps: " + ", ".join(gaps)
    return result.findings[0] if result.findings else "Needs review"


def print_live_result(index: int, total: Optional[int], result: AuditResult, args: argparse.Namespace) -> None:
    enabled = color_enabled(args)
    status_color = {"PASS": "green", "REVIEW": "yellow", "SKIP": "blue", "ERROR": "red"}[result.status]
    status_icon = {"PASS": "🟢", "REVIEW": "🟡", "SKIP": "🔵", "ERROR": "🔴"}[result.status]
    counter = f"[{index}/{total}]" if total is not None else f"[{index}]"
    reason = result_reason(result)
    reason_display = reason if len(reason) <= 38 else reason[:35] + "..."
    name_display = result.name if len(result.name) <= 32 else result.name[:29] + "..."
    status = paint(f"{status_icon} {result.status:<6}", status_color, enabled, bold=True)
    print(
        f"{counter:<9} [Depth {result.depth}]  "
        f"Auditing Document: {name_display:<32} -> {status} "
        f"[{result.doc_type:<14} | {result.tokens:>6,} tokens | {reason_display:<38}]"
    )
    print(f"           {paint('↳', 'cyan', enabled)} {result.url}")


def fetch_with_retry(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 2,
    base_delay: float = 0.75,
) -> str:
    """Fetch a UTF-8 document with bounded retry behavior."""
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401, 403, 404, 410):
                break
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc

        if attempt < max_retries - 1:
            time.sleep(base_delay * (2**attempt))

    raise RuntimeError(f"fetch failed: {last_error}")


def normalize_doc_url(base_url: str, raw_href: str) -> Optional[str]:
    """Normalize a Snowflake documentation link to its public Markdown endpoint."""
    clean_href = raw_href.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean_href or clean_href.startswith(("mailto:", "javascript:", "data:")):
        return None
    if any(char in clean_href for char in "[]<>"):
        return None

    target = (
        clean_href
        if clean_href.startswith(("http://", "https://"))
        else urljoin(base_url, clean_href)
    )
    parsed = urlparse(target)
    if parsed.scheme != "https" or parsed.netloc != ALLOWED_DOMAIN:
        return None

    path = parsed.path
    if path.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".css", ".js", ".svg")
    ):
        return None
    if path.endswith(("/llms.txt", "/llms-full.txt")):
        return None
    if path.endswith(".md"):
        return f"https://{ALLOWED_DOMAIN}{path}"

    clean_path = path.rstrip("/")
    if clean_path.endswith(".html"):
        clean_path = clean_path[:-5]
    if not clean_path.startswith("/en/"):
        clean_path = "/en" + clean_path
    return f"https://{ALLOWED_DOMAIN}{clean_path}.md"


def extract_child_md_links(content: str, base_url: str) -> set[str]:
    raw_links = re.findall(r"\[[^\]]*\]\(([^\s\)\"]+)\)", content)
    raw_links.extend(BARE_URL_RE.findall(content))
    return {
        normalized
        for raw in raw_links
        if (normalized := normalize_doc_url(base_url, raw)) is not None
    }


def detect_signals(content: str) -> Signals:
    return Signals(
        executable_code=bool(EXECUTABLE_FENCE_RE.search(content)),
        privilege_guidance=bool(
            PRIVILEGE_RE.search(content) or SECURITY_LINK_RE.search(content)
        ),
        version_bounds=bool(VERSION_BOUND_RE.search(content)),
        outbound_links=bool(MARKDOWN_LINK_RE.search(content) or BARE_URL_RE.search(content)),
        heading_count=len(re.findall(r"^#{1,6}\s+\S", content, re.MULTILINE)),
    )


def classify_document(url: str, content: str, tokens: int, signals: Signals) -> str:
    path = urlparse(url).path.lower()
    name = Path(path).stem
    heading_match = re.search(r"^#{1,2}\s+(.+)$", content, re.MULTILINE)
    first_heading = heading_match.group(1).lower() if heading_match else ""

    overview_markers = (
        name in {"index", "overview", "appendices", "other-resources"}
        or "guides-overview" in name
        or "getting-started" in name
        or any(word in first_heading for word in ("overview", "getting started"))
    )
    if overview_markers:
        return "OVERVIEW"

    reference_markers = (
        "/reference" in path
        or "sql-reference" in path
        or name.endswith("-api")
        or name in {"api-reference", "data-types", "functions", "commands", "syntax"}
    )
    if reference_markers:
        return "REFERENCE"

    if tokens < 1_200 and signals.outbound_links and not signals.executable_code:
        return "OVERVIEW"

    return "IMPLEMENTATION"


def evaluate_document(url: str, content: str, tokens: int) -> tuple[str, str, list[str], Signals]:
    signals = detect_signals(content)
    doc_type = classify_document(url, content, tokens, signals)
    name = Path(urlparse(url).path).stem.lower()
    findings: list[str] = []

    if doc_type == "OVERVIEW":
        threshold = (
            THRESHOLDS["root_index_tokens"]
            if name == "index"
            else THRESHOLDS["overview_tokens"]
        )
        if tokens > threshold:
            findings.append(f"consider splitting or improving routing ({tokens:,}>{threshold:,} tokens)")
        if not signals.outbound_links:
            findings.append("no outbound routing links detected")

    elif doc_type == "REFERENCE":
        threshold = THRESHOLDS["reference_tokens"]
        if tokens > threshold:
            findings.append(f"consider chunking the reference ({tokens:,}>{threshold:,} tokens)")

    else:
        if not signals.executable_code:
            findings.append("no executable-language code block detected")
        if not signals.privilege_guidance:
            findings.append("no local privilege guidance or canonical security link detected")
        if not signals.version_bounds:
            findings.append("no explicit SDK or dependency version bound detected")
        threshold = THRESHOLDS["implementation_tokens"]
        if tokens > threshold:
            findings.append(f"consider chunking the implementation guide ({tokens:,}>{threshold:,} tokens)")

    return doc_type, ("REVIEW" if findings else "PASS"), findings, signals


def audit_url(url: str, depth: int, timeout: float, encoder) -> tuple[AuditResult, Optional[str]]:
    path = Path(urlparse(url).path)
    name = path.stem or "document"
    if name in {"index", "reference"}:
        without_suffix = path.with_suffix("")
        parts = [
            part
            for part in without_suffix.parts
            if part not in {"/", "en", "developer-guide"}
        ]
        if len(parts) > 1:
            name = "/".join(parts[-3:])
    try:
        content = fetch_with_retry(url, timeout=timeout)
        tokens = len(encoder.encode(content))
        doc_type, status, findings, signals = evaluate_document(url, content, tokens)
        return AuditResult(name, url, depth, doc_type, tokens, status, findings, signals), content
    except Exception as exc:
        if "HTTP Error 404" in str(exc) and url.endswith(".md"):
            html_url = url[:-3]
            try:
                fetch_with_retry(html_url, timeout=timeout, max_retries=1)
                finding = "HTML-only reference surface; excluded from Markdown structural scoring"
                return AuditResult(
                    name, url, depth, "HTML_ONLY", 0, "SKIP", [finding]
                ), None
            except Exception as html_exc:
                if "HTTP Error 404" in str(html_exc):
                    finding = "discovered route returns 404 in both Markdown and HTML; verify or remove the link"
                    return AuditResult(
                        name, url, depth, "BROKEN_ROUTE", 0, "REVIEW", [finding]
                    ), None
        return AuditResult(name, url, depth, "FETCH_ERROR", 0, "ERROR", [], error=str(exc)), None


def load_manifest(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        normalized = normalize_doc_url(ROOT_URL, candidate)
        if normalized is None:
            raise ValueError(f"unsupported manifest URL: {candidate}")
        urls.append(normalized)
    return urls


def crawl(args: argparse.Namespace) -> list[AuditResult]:
    encoder = tiktoken.get_encoding("cl100k_base")
    recursive = args.manifest is None

    if args.manifest:
        seed_urls = load_manifest(args.manifest)
    else:
        root_content = fetch_with_retry(ROOT_URL, timeout=args.timeout, max_retries=3)
        seed_urls = sorted(extract_child_md_links(root_content, ROOT_URL))
        if not seed_urls:
            raise RuntimeError("no valid Markdown endpoints discovered in llms.txt")

    queue = deque((url, 1) for url in seed_urls)
    visited: set[str] = set()
    results: list[AuditResult] = []
    verbose = args.format == "text"
    if verbose:
        print_scan_header(args, len(seed_urls))
    expected_total = min(len(seed_urls), args.max_files) if args.manifest else args.max_files

    while queue and len(results) < args.max_files:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        result, content = audit_url(url, depth, args.timeout, encoder)
        results.append(result)
        if verbose:
            print_live_result(len(results), expected_total, result, args)

        if recursive and content is not None:
            for child_url in sorted(extract_child_md_links(content, url)):
                if child_url not in visited:
                    queue.append((child_url, depth + 1))
        if args.delay:
            time.sleep(args.delay)
    return results


def summarize(results: Iterable[AuditResult]) -> dict[str, object]:
    result_list = list(results)
    counts = {
        status: sum(result.status == status for result in result_list)
        for status in ("PASS", "REVIEW", "SKIP", "ERROR")
    }
    evaluated = counts["PASS"] + counts["REVIEW"] + counts["ERROR"]
    signal = (counts["PASS"] / evaluated * 100) if evaluated else 0.0
    return {
        "documents": len(result_list),
        "evaluated_documents": evaluated,
        "counts": counts,
        "structural_readiness_signal": round(signal, 1),
        "note": "Structural proxy only; validate priority findings with representative agent tasks.",
    }


def print_text_report(
    results: list[AuditResult], summary: dict[str, object], args: argparse.Namespace
) -> None:
    enabled = color_enabled(args)
    counts = summary["counts"]
    score = summary["structural_readiness_signal"]
    print()
    print(paint("╭──────────────────────── FINAL FLEET REPORT ────────────────────────╮", "cyan", enabled, bold=True))
    print(
        "│  "
        f"{paint('✓ PASS', 'green', enabled, bold=True)} {counts['PASS']:<4}  "
        f"{paint('! REVIEW', 'yellow', enabled, bold=True)} {counts['REVIEW']:<4}  "
        f"{paint('↷ SKIP', 'blue', enabled, bold=True)} {counts['SKIP']:<4}  "
        f"{paint('× ERROR', 'red', enabled, bold=True)} {counts['ERROR']:<4}"
    )
    print(f"│  Structural readiness signal: {paint(f'{score:.1f}%', 'cyan', enabled, bold=True)}")
    print(paint("╰────────────────────────────────────────────────────────────────────╯", "cyan", enabled))
    print(f"\n{paint('Interpretation', 'cyan', enabled, bold=True)}")
    print(f"  {summary['note']}")
    if counts["REVIEW"]:
        print(f"\n{paint('Review queue', 'yellow', enabled, bold=True)}")
        for result in results:
            if result.status == "REVIEW":
                print(
                    f"  {paint('→', 'yellow', enabled)} "
                    f"{paint(result.name, 'yellow', enabled, bold=True)}  "
                    f"[{result.doc_type} · depth {result.depth} · {result.tokens:,} tokens]"
                )
                for finding in result.findings:
                    print(f"      {finding}")
                print(f"      {result.url}")
    if counts["SKIP"]:
        print(f"\n{paint('Skipped surfaces', 'blue', enabled, bold=True)}")
        print("  HTML-only references are visible here but excluded from the readiness score.")
        for result in results:
            if result.status == "SKIP":
                print(f"  {paint('↷', 'blue', enabled)} {result.name}  {result.url}")
    if counts["ERROR"]:
        print(f"\n{paint('Fetch errors', 'red', enabled, bold=True)}")
        for result in results:
            if result.status == "ERROR":
                print(f"  {paint('×', 'red', enabled)} {result.name}: {result.error}")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        results = crawl(args)
    except Exception as exc:
        print(f"Scanner failed: {exc}", file=sys.stderr)
        return 1

    summary = summarize(results)
    if args.format == "json":
        print(json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=2))
    else:
        print_text_report(results, summary, args)

    if summary["counts"]["ERROR"]:
        return 1
    if args.fail_on_review and summary["counts"]["REVIEW"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
