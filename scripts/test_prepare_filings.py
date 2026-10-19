"""Offline behavioral tests; synthetic embeddings are test fixtures only."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from prepare_filings import (Embeddings, build_chunks, digest, embed_records, extract_html,
                             narrative_units, parents, semantic_groups, table_chunks, tokens, windows)
from load_pinecone import ROOT, read_records


class PreparationTests(unittest.TestCase):
    def test_extraction_preserves_visible_facts_and_excludes_hidden_resources(self):
        html = '''<html><head><style>badstyle</style></head><body>
        <ix:header><ix:hidden>HIDDEN_XBRL</ix:hidden></ix:header>
        <div style="display:none">SECRET</div><div id="mdna">ITEM 7. Management Discussion</div>
        <p>Revenue was <ix:nonFraction scale="6">12,345</ix:nonFraction> million.</p>
        <table id="sales"><tr><th rowspan="2">Metric</th><th colspan="2">USD millions</th></tr>
        <tr><th>2026</th><th>2025</th></tr><tr><td>Revenue</td><td>12,345</td><td>10,000</td></tr></table>
        <p>Footnote: consolidated sales.</p></body></html>'''
        blocks = extract_html(html)
        text = " ".join(b.text for b in blocks)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("HIDDEN_XBRL", text)
        self.assertIn("12,345 million", text)
        table = next(b for b in blocks if b.kind == "table")
        self.assertIn("USD millions | USD millions", table.text)
        self.assertIn("2026 | 2025", table.text)
        self.assertEqual(table.anchor, "sales")
        self.assertIn("ITEM 7", table.section)

    def test_fixed_windows_preserve_unicode_and_cover_source(self):
        text = "Revenue rose 📈; café and 日本語。 " * 100
        parts = list(windows(text, 60, 10))
        self.assertGreater(len(parts), 2)
        coverage = set()
        for part, start, end in parts:
            self.assertEqual(part, text[start:end])
            self.assertNotIn("\ufffd", part)
            self.assertLessEqual(len(tokens(part)), 62)
            coverage.update(range(start, end))
        self.assertEqual(len(coverage), len(text))

    def test_semantic_boundaries_change_with_embedding_similarity(self):
        units = [(f"Sentence {i} contains financial information.", i * 50, (i + 1) * 50) for i in range(8)]
        similar = [[1., 0.]] * 8
        shifted = [[1., 0.]] * 4 + [[0., 1.]] * 4
        plain = semantic_groups(units, similar, 10, 100, 150, 0, 70)
        split = semantic_groups(units, shifted, 10, 100, 150, 0, 70)
        self.assertEqual(plain, [(0, 8)])
        self.assertEqual(split, [(0, 4), (4, 8)])

    def test_semantic_bounds_overlap_and_full_coverage(self):
        text = "Revenue increased substantially during the current fiscal quarter. " * 200
        units = narrative_units(text)
        groups = semantic_groups(units, [[1., 0.]] * len(units), 50, 100, 130, 20, 80)
        seen = set()
        for a, b in groups:
            seen.update(range(a, b))
            self.assertLessEqual(len(tokens(" ".join(u[0] for u in units[a:b]))), 130)
        self.assertEqual(seen, set(range(len(units))))

    def test_table_chunks_repeat_headers_and_keep_last_row(self):
        parent = {"text": "Metric | 2026 | 2025\nUSD millions | |\nUnaudited | |\n" + "\n".join(f"Metric{i} | {i} | {i+1}" for i in range(100)), "context": "Revenue by product"}
        chunks = table_chunks(parent, 200)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all("Metric | 2026 | 2025" in chunk for chunk in chunks))
        self.assertIn("Metric99", chunks[-1])
        self.assertTrue(all(len(tokens(chunk)) <= 200 for chunk in chunks))

    def test_embedding_cache_deduplicates_and_reorders_response(self):
        calls = []
        def request(texts):
            calls.append(texts)
            return {"model": "test-model", "data": [{"index": i, "embedding": [i + 1., 1.]} for i in reversed(range(len(texts)))], "usage": {"total_tokens": 3}}
        with tempfile.TemporaryDirectory() as directory:
            embedder = Embeddings(Path(directory) / "cache.db", "test-model", 2, request=request)
            try:
                result = embedder.get_many(["alpha", "beta", "alpha"])
                self.assertEqual(len(calls), 1)
                np.testing.assert_array_equal(result[0], result[2])
                np.testing.assert_array_equal(result[1], [2, 1])
                embedder.get_many(["alpha"])
                self.assertEqual(len(calls), 1)
            finally:
                embedder.close()

    def test_invalid_response_never_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            embedder = Embeddings(Path(directory) / "cache.db", "test", 2, request=lambda _: {"model": "test", "data": [{"index": 0, "embedding": [float("nan"), 1.]}]})
            try:
                with self.assertRaises(ValueError):
                    embedder.get_many(["input"])
                self.assertEqual(embedder.db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 0)
            finally:
                embedder.close()

    def test_export_is_accepted_by_existing_loader(self):
        config = json.loads((ROOT / "pinecone/index.json").read_text())
        catalog = json.loads((ROOT / "data/filings.json").read_text())
        company = next(c for c in catalog["companies"] if c["tickers"][0] == "NVDA")
        doc = {**company["filings"][0], "company": company["name"], "ticker": "NVDA", "cik": company["cik"], "sha256": "test"}
        settings = {"model": config["tags"]["embedding-model"], "dimensions": 1536, "fingerprint": digest("test"), "max_tokens": 1200, "target_tokens": 700, "min_tokens": 350, "overlap": 100, "percentile": 80}
        chunks = build_chunks(doc, parents(extract_html("<p>Revenue rose during this fiscal year.</p>")), "fixed", settings)
        def request(texts):
            return {"model": settings["model"], "data": [{"index": i, "embedding": [.01] * 1536} for i in range(len(texts))]}
        with tempfile.TemporaryDirectory() as directory:
            embedder = Embeddings(Path(directory) / "cache.db", settings["model"], 1536, request=request)
            try:
                path = Path(directory) / "fixed.jsonl"
                path.write_text("\n".join(json.dumps(r) for r in embed_records(chunks, embedder)), encoding="utf-8")
                self.assertEqual(len(read_records(path, config, "fixed", catalog)), 1)
            finally:
                embedder.close()

    def test_semantic_export_preserves_canonical_text_ranges(self):
        config = json.loads((ROOT / "pinecone/index.json").read_text())
        catalog = json.loads((ROOT / "data/filings.json").read_text())
        company = next(c for c in catalog["companies"] if c["tickers"][0] == "NVDA")
        doc = {**company["filings"][0], "company": company["name"], "ticker": "NVDA", "cik": company["cik"], "sha256": "test"}
        text = ("Revenue increased over the previous fiscal period. " * 80 +
                "Litigation creates significant regulatory and operational risks. " * 80)
        source = parents(extract_html(f"<p>{text}</p>"))
        settings = {"model": config["tags"]["embedding-model"], "dimensions": 1536, "fingerprint": digest("test"), "max_tokens": 1200, "target_tokens": 700, "min_tokens": 350, "overlap": 100, "percentile": 80}
        class TestEmbedder:
            def get_many(self, inputs):
                return [[1., 0.] if "Litigation" not in value else [0., 1.] for value in inputs]
        chunks = build_chunks(doc, source, "semantic", settings, TestEmbedder())
        self.assertGreater(len(chunks), 1)
        seen = set()
        for chunk in chunks:
            meta = chunk["metadata"]
            self.assertEqual(meta["strategy"], "semantic")
            seen.update(range(meta["char_start"], meta["char_end"]))
            self.assertLessEqual(meta["token_count"], 1200)
        missing = [i for i in range(len(source[0]["text"])) if i not in seen and not source[0]["text"][i].isspace()]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
