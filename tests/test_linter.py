import unittest

import tiktoken

import agent_fleet_linter as linter


class LinterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoder = tiktoken.get_encoding("cl100k_base")

    def evaluate(self, url, content):
        tokens = len(self.encoder.encode(content))
        return linter.evaluate_document(url, content, tokens)

    def test_text_fence_and_incidental_words_do_not_create_false_pass(self):
        content = """# Configure the connector

```text
hello
```

The role of this guide is version control.
"""
        _, status, findings, signals = self.evaluate(
            "https://docs.snowflake.com/en/developer-guide/example.md", content
        )
        self.assertEqual(status, "REVIEW")
        self.assertFalse(signals.executable_code)
        self.assertFalse(signals.privilege_guidance)
        self.assertFalse(signals.version_bounds)
        self.assertGreaterEqual(len(findings), 3)

    def test_explicit_implementation_signals_pass(self):
        content = """# Install and configure

Install a tested connector version:

```bash
pip install snowflake-connector-python==3.12.0
```

Use least-privilege access. See the
[access-control guide](https://docs.snowflake.com/en/user-guide/security-access-control-overview.md).
"""
        _, status, findings, signals = self.evaluate(
            "https://docs.snowflake.com/en/developer-guide/example.md", content
        )
        self.assertEqual(status, "PASS")
        self.assertEqual(findings, [])
        self.assertTrue(signals.executable_code)
        self.assertTrue(signals.privilege_guidance)
        self.assertTrue(signals.version_bounds)

    def test_small_link_router_is_classified_as_overview(self):
        content = """# Connector guide

- [Install](https://docs.snowflake.com/en/developer-guide/example-install.md)
- [API](https://docs.snowflake.com/en/developer-guide/example-api.md)
"""
        doc_type, status, _, _ = self.evaluate(
            "https://docs.snowflake.com/en/developer-guide/example-driver.md", content
        )
        self.assertEqual(doc_type, "OVERVIEW")
        self.assertEqual(status, "PASS")

    def test_explicit_reference_path_wins_over_small_page_heuristic(self):
        content = """# Data types

- [Numeric](https://docs.snowflake.com/en/sql-reference/data-types-numeric.md)
"""
        doc_type, status, _, _ = self.evaluate(
            "https://docs.snowflake.com/en/data-types.md", content
        )
        self.assertEqual(doc_type, "REFERENCE")
        self.assertEqual(status, "PASS")

    def test_external_domains_are_rejected(self):
        self.assertIsNone(
            linter.normalize_doc_url(
                linter.ROOT_URL, "https://example.com/untrusted.md"
            )
        )

    def test_summary_keeps_errors_visible(self):
        results = [
            linter.AuditResult("a", "https://docs.snowflake.com/en/a.md", 1, "OVERVIEW", 10, "PASS", []),
            linter.AuditResult("b", "https://docs.snowflake.com/en/b.md", 1, "UNKNOWN", 0, "ERROR", [], error="timeout"),
        ]
        summary = linter.summarize(results)
        self.assertEqual(summary["counts"]["ERROR"], 1)
        self.assertEqual(summary["structural_readiness_signal"], 50.0)


if __name__ == "__main__":
    unittest.main()
