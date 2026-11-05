from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from service_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider


class MarkdownKnowledgeTests(unittest.TestCase):
    def test_demo_address_is_retrieved_with_source(self):
        root = Path(__file__).resolve().parents[1]
        provider = MarkdownKnowledgeProvider(root / "examples" / "knowledge")
        result = provider.search("示例门店地址", limit=1)
        self.assertEqual(result[0]["source"], "service.md")
        self.assertIn("示例市", result[0]["text"])

    def test_heading_match_has_higher_weight_than_body(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "demo.md").write_text(
                "# billing\nDemo policy.\n# general\nAsk about billing here.\n",
                encoding="utf-8",
            )
            provider = MarkdownKnowledgeProvider(directory)
            self.assertEqual(provider.search("billing")[0]["title"], "billing")

    def test_empty_or_unmatched_query_has_no_result(self):
        root = Path(__file__).resolve().parents[1]
        provider = MarkdownKnowledgeProvider(root / "examples" / "knowledge")
        self.assertEqual(provider.search(""), [])
        self.assertEqual(provider.search("no-such-term"), [])


if __name__ == "__main__":
    unittest.main()
