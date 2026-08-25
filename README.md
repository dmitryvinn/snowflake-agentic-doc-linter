# ❄️ Snowflake Agentic Documentation Structural Scanner

A small synthetic-evaluation harness that follows Snowflake's public
[`llms.txt`](https://docs.snowflake.com/llms.txt) documentation graph and surfaces
structural risks that may make content harder for coding agents to discover,
retrieve, and use safely.

This tool produces **review signals**, not a verdict that a document or product
is "agent-ready." Priority findings should be validated with representative
agent tasks and human review.

## What it evaluates

The scanner classifies pages as `OVERVIEW`, `REFERENCE`, or `IMPLEMENTATION`
and applies transparent checks appropriate to that document type.

- **Overview:** routing links and context size.
- **Reference:** context size and likely chunking pressure.
- **Implementation:** executable-language code blocks, explicit SDK or dependency
  bounds, privilege guidance or a canonical security link, and context size.
- **All requests:** fetch failures are reported as `ERROR`; they are never silently
  removed from the denominator.

Statuses are deliberately non-judgmental:

- `PASS`: the configured structural checks were satisfied.
- `REVIEW`: one or more heuristic findings should be inspected.
- `ERROR`: the page could not be evaluated.

## Run the interview demo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 agent_fleet_linter.py --max-files 50
```

Text mode uses color by default and prints every file as it is processed, including
depth, document type, token count, status, a concise reason, and the full URL on the
following line. The final report summarizes the fleet and expands only the pages
that need review.

Use `--no-color` only for plain-text logs. A deterministic manifest mode remains
available for CI and troubleshooting, but it is not required for the presentation.

## Crawl the public documentation graph

```bash
# Human-readable recursive scan with live progress and color
python3 agent_fleet_linter.py --max-files 50

# Machine-readable report
python3 agent_fleet_linter.py --max-files 50 --format json > report.json

# CI-style behavior
python3 agent_fleet_linter.py --max-files 50 --fail-on-review

# Backward-compatible positional limit
python3 agent_fleet_linter.py 50
```

## Run on demand with GitHub Actions

The repository includes
[`run-demo.yml`](.github/workflows/run-demo.yml), a manually triggered workflow.

1. Open the repository's **Actions** tab.
2. Select **Run Agentic Documentation Demo**.
3. Choose **Run workflow**.

The workflow defaults to `50` files. You can change the limit in the workflow
dialog without editing the YAML. The complete colorized scan is shown in the
**Run recursive fleet scanner** step. The job fails only for actual scanner or
fetch errors; `REVIEW` and `SKIP` remain visible without failing the demo.

## Audit an explicit manifest

Create a newline-delimited file containing public Snowflake documentation URLs:

```text
https://docs.snowflake.com/en/api-reference.md
https://docs.snowflake.com/en/index.md
```

Then run:

```bash
python3 agent_fleet_linter.py --manifest my_manifest.txt
```

## Limitations

- Classification and quality gates are heuristics.
- Token thresholds are configurable hypotheses, not universal context-window laws.
- `cl100k_base` is an approximation and does not represent every model or retrieval system.
- Structural checks can produce false positives and false negatives.
- A local security link may be preferable to repeating RBAC instructions on every page.
- Citation, retrieval, and structure do not prove successful task completion.
- The next evaluation layer should run representative agent tasks and measure
  correctness, groundedness, permissions, retries, and completion.

## Run tests

```bash
python3 -m unittest discover -s tests -v
```

## Responsible use

The scanner is a prototype for prioritizing investigation. A `REVIEW` result is
not a declaration that Snowflake documentation is incorrect. It is a transparent
hypothesis that Documentation, Product, Security, DevRel, or the rule owner can
accept, reject, or refine.
