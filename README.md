# ❄️ Snowflake Agentic DX Fleet Scanner

An automated documentation linter designed to evaluate the **Agent Readiness** of the Snowflake documentation ecosystem by recursively crawling `llms.txt` and applying context-window quality gates.

## 🎯 Purpose

AI coding assistants (Cursor, Claude Code, GitHub Copilot) rely on `llms.txt` indices to navigate technical documentation. Poorly structured, oversized, or missing security metadata degrades agent output quality. This tool audits the ecosystem to ensure documentation is optimized for LLM context windows.

## 📐 Document-Type Quality Taxonomy

* **`OVERVIEW`** (Limit: < 2,500 tokens): Must contain outbound routing links. Fails on context bloat or index bloat (>1,500 tokens for root index pages).
* **`REFERENCE`** (Limit: < 4,000 tokens): Must contain concise syntax/API specs. Fails on bloat.
* **`IMPLEMENTATION`** (Limit: Flexible): Requires executable code blocks (```), RBAC privilege checks, and explicit SDK version bounds.

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run default audit batch (5 documents)
python3 agent_fleet_linter.py

# 3. Run recursive BFS crawl across child links (e.g., 50 documents)
python3 agent_fleet_linter.py 50