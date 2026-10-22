"""Cached token statistics; scoring retains scope-specific BM25 semantics."""

import math
import re
import threading
from collections import Counter, OrderedDict
from .telemetry import span

STOP = set("the a an of and in to is for what how was were did it its on".split())


def terms(text):
    return [t for t in re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", text.lower()) if t not in STOP]


class LexicalIndex:
    def __init__(self, records, prepared=None):
        self.records = records
        self.counts = (
            prepared
            if prepared is not None
            else {r["id"]: Counter(terms(r["metadata"]["text"])) for r in records}
        )

    def score(self, query, limit=20):
        words = terms(query)
        docs = [self.counts[r["id"]] for r in self.records]
        lengths = [sum(d.values()) for d in docs]
        avg = sum(lengths) / (len(docs) or 1)
        df = {w: sum(w in d for d in docs) for w in words}
        ranked = []
        for row, freq, length in zip(self.records, docs, lengths):
            score = sum(
                math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                * freq[w]
                * 2.2
                / (freq[w] + 1.2 * (0.25 + 0.75 * length / (avg or 1)))
                for w in words
            )
            if score > 0:
                ranked.append({**row, "score": score})
        return sorted(ranked, key=lambda r: -r["score"])[:limit]


class CorpusCache:
    def __init__(self, capacity=8):
        self.capacity, self.entries, self.lock = capacity, OrderedDict(), threading.Lock()

    def load(self, store, key):
        path = store.path(key)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return [], {}
        signature = (str(path), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if signature in self.entries:
                self.entries.move_to_end(signature)
                with span("corpus_cache_hit"):
                    return self.entries[signature]
            with span("corpus_load"):
                rows = store.json(key, [])
                value = rows, LexicalIndex(rows).counts
            self.entries[signature] = value
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
            return value


CORPORA = CorpusCache()
