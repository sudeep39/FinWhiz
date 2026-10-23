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
    """BM25 scorer over a caller-selected set of chunk records.

    CorpusCache prepares reusable token counts; retrieval constructs a scoped
    index so keyword scores use only the authorized company/period corpus."""
    def __init__(self, records, prepared=None):
        """Bind chunk records and optional precomputed per-chunk term counts.

        Called by CorpusCache and retrieval. Missing prepared counts are computed
        locally; construction does not contact an embedding or model provider."""
        self.records = records
        self.counts = (
            prepared
            if prepared is not None
            else {r["id"]: Counter(terms(r["metadata"]["text"])) for r in records}
        )

    def score(self, query, limit=20):
        """Return at most limit positively scoring chunks, highest BM25 score first.

        Called by retrieval for a question. Uses this instance's scoped records
        for document frequency/average length; returned records include score."""
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
    """Thread-safe LRU cache of chunk records and token counts.

    The module-level CORPORA instance is used by retrieval.retrieve. Entries
    are invalidated by file path, modification time and size."""
    def __init__(self, capacity=8):
        """Create an LRU with capacity measured in corpus entries, not bytes.

        Used for the module-level CORPORA instance; initializes an empty cache
        and lock without reading any artifacts."""
        self.capacity, self.entries, self.lock = capacity, OrderedDict(), threading.Lock()

    def load(self, store, key):
        """Return (chunk records, term counts) for a logical corpus key.

        Called by retrieval.retrieve. Cache misses read/tokenize outside the
        global lock; changed signatures replace older entries for the same path.
        An absent artifact returns ([], {}); other read/parse errors propagate."""
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
        # Disk I/O and tokenization must not serialize unrelated cold requests.
        with span("corpus_load"):
            rows = store.json(key, [])
            value = rows, LexicalIndex(rows).counts
        with self.lock:
            if signature in self.entries:
                self.entries.move_to_end(signature)
                return self.entries[signature]
            for old in [old for old in self.entries if old[0] == signature[0]]:
                del self.entries[old]
            self.entries[signature] = value
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
            return value


CORPORA = CorpusCache()
