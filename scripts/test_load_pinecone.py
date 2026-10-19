"""Offline validation and mocked Pinecone tests; never contacts Pinecone."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from load_pinecone import ROOT, batches, encode, ensure_index, read_records


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "pinecone/index.json").read_text())
        self.catalog = json.loads((ROOT / "data/filings.json").read_text())
        filing = next(c for c in self.catalog["companies"] if c["tickers"][0] == "NVDA")["filings"][0]
        # Synthetic vector for offline tests only; not a financial embedding.
        self.record = {"id": "test-1", "values": [0.01] * 1536, "metadata": {
            "ticker": "NVDA", "accession": filing["accession"], "source_url": filing["url"],
            "text": "Offline test text", "strategy": "fixed", "embedding_model": "text-embedding-3-large",
        }}

    def read(self, records, strategy="fixed"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vectors.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            return read_records(path, self.config, strategy, self.catalog)

    def test_valid_records_and_byte_bounded_batches(self):
        records = []
        for number in range(5):
            record = copy.deepcopy(self.record)
            record["id"] = f"test-{number}"
            records.append(record)
        self.assertEqual(len(self.read(records)), 5)
        payloads = list(batches(records, "sec-fixed-test", max_records=2, max_bytes=30000))
        self.assertEqual(sum(len(p["vectors"]) for p in payloads), 5)
        self.assertTrue(all(len(encode(p)) <= 30000 for p in payloads))

    def test_rejects_dimensions_nan_and_zero_vectors(self):
        for values in ([1], [float("nan")] * 1536, [0] * 1536):
            record = copy.deepcopy(self.record)
            record["values"] = values
            with self.assertRaises(ValueError):
                self.read([record])

    def test_rejects_wrong_strategy_model_and_company(self):
        for field, value in (("strategy", "semantic"), ("embedding_model", "other"), ("ticker", "JPM")):
            record = copy.deepcopy(self.record)
            record["metadata"][field] = value
            with self.assertRaises(ValueError):
                self.read([record])

    def test_rejects_duplicate_ids_and_nested_metadata(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.read([self.record, self.record])
        self.record["metadata"]["nested"] = {"field": "value"}
        with self.assertRaisesRegex(ValueError, "flat"):
            self.read([self.record])

    def test_creates_and_waits_for_index(self):
        ready = {**self.config, "status": {"ready": True}, "host": "test.svc.pinecone.io"}
        replies = [(404, {}), (201, {}), (200, ready)]
        calls = []

        class API:
            def request(self, *args, **kwargs):
                calls.append(args)
                return replies.pop(0)

        self.assertEqual(ensure_index(API(), self.config, create=True, sleep=lambda _: None), ready["host"])
        self.assertEqual(calls[1][0], "POST")

    def test_missing_index_requires_creation_flag(self):
        class API:
            def request(self, *args, **kwargs):
                return 404, {}
        with self.assertRaisesRegex(ValueError, "--create-index"):
            ensure_index(API(), self.config)

    def test_existing_incompatible_index_is_rejected(self):
        index = {**self.config, "dimension": 3072}
        class API:
            def request(self, *args, **kwargs):
                return 200, index
        with self.assertRaisesRegex(ValueError, "dimension"):
            ensure_index(API(), self.config)


if __name__ == "__main__":
    unittest.main()
