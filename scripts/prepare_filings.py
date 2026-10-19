"""Parse SEC HTML, compare token/semantic chunking, and export Pinecone JSONL.

Default: offline extraction and fixed chunks. --embed enables paid OpenAI calls
and produces both fixed and embedding-based semantic retrieval records.
"""
import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time
import warnings
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning
import numpy as np
import tiktoken

from load_pinecone import ROOT, TICKERS, EXPERIMENT, NoRedirect, encode, read_records

VERSION = "html-v1"
ENC = None


def tokenizer():
    global ENC
    if ENC is None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / ".cache/tiktoken"))
        ENC = tiktoken.get_encoding("cl100k_base")
    return ENC


def tokens(text):
    return tokenizer().encode(text, disallowed_special=())


def normalize(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value)).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(encode(value) + b"\n")
    temp.replace(path)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as output:
        for record in records:
            output.write(encode(record) + b"\n")
    temp.replace(path)


@dataclass
class Block:
    kind: str
    text: str
    anchor: str
    section: str = "Cover"
    block_id: str = ""


def table_rows(table):
    """Expand merged cells into a rectangular grid; retain explicit row boundaries."""
    grid, spans = [], {}
    for row_index, row in enumerate(table.find_all("tr")):
        if row.find_parent("table") is not table:
            continue
        cells = {}
        for (r, c), value in list(spans.items()):
            if r == row_index:
                cells[c] = value
        col = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while col in cells:
                col += 1
            value = normalize(cell.get_text(" ", strip=True))
            try:
                width = min(100, max(1, int(cell.get("colspan", 1))))
                height = min(100, max(1, int(cell.get("rowspan", 1))))
            except ValueError:
                width = height = 1
            for offset in range(width):
                cells[col + offset] = value
                for down in range(1, height):
                    spans[row_index + down, col + offset] = value
            col += width
        if cells:
            grid.append([cells.get(c, "") for c in range(max(cells) + 1)])
    if not grid:
        return []
    width = max(map(len, grid))
    grid = [row + [""] * (width - len(row)) for row in grid]
    columns = [i for i in range(width) if any(row[i] for row in grid)]
    # SEC tables use many spacer columns and colspans. Merge only columns that
    # are identical across every row, retaining differing period/value columns.
    keep = []
    for column in columns:
        if not keep or any(row[column] != row[keep[-1]] for row in grid):
            keep.append(column)
    return [[row[i] for i in keep] for row in grid if any(row)]


def extract_html(html):
    # SEC inline-XBRL XHTML frequently has an XML declaration. HTML parsing is
    # deliberate: tolerate publisher HTML quirks while retaining ix:* visible text.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for element in list(soup.find_all(True)):
        if element.attrs is None:
            continue
        style = element.get("style", "")
        if (element.name in {"script", "style", "head", "ix:header", "ix:hidden", "noscript"}
                or element.has_attr("hidden") or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I)):
            element.decompose()
    blocks = []
    containers = {"div", "p", "table", "ul", "ol", "li", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "td", "th"}

    def emit(text, node, anchor, kind="narrative"):
        text = normalize(text)
        if not text or re.fullmatch(r"\d{1,4}|Table of Contents", text, re.I):
            return
        if text.startswith("PART ") and len(text) < 100:
            kind = "heading"
        elif re.match(r"^ITEM\s+\d+[A-Z]?(?:[. :]|$)", text, re.I) and len(text) < 220:
            kind = "heading"
        elif len(text) < 160 and (node.name in {"h1", "h2", "h3", "h4", "h5", "h6"} or
                (sum(len(normalize(e.get_text())) for e in node.find_all(
                    lambda t: t.name in {"b", "strong"} or bool(re.search(r"font-weight\s*:\s*(?:bold|[7-9]00)", t.get("style", "")))))
                 >= len(text) * .8 and not re.search(r"[.!?]$", text))):
            kind = "heading"
        blocks.append(Block(kind, text, anchor))

    def walk(node, anchor=""):
        if not isinstance(node, Tag):
            return
        anchor = node.get("id") or node.get("name") or anchor
        if node.name == "table" and not node.find("table"):
            text = normalize(node.get_text(" ", strip=True))
            links = node.find_all("a", href=re.compile(r"^#"))
            if len(links) >= 5 and sum(len(a.get_text()) for a in links) > len(text) * .4:
                return  # navigation table, not source disclosures
            rows = table_rows(node)
            if len(rows) >= 2 and max(map(len, rows), default=0) >= 2:
                caption = node.find("caption")
                caption_text = normalize(caption.get_text(" ", strip=True)) + "\n" if caption else ""
                blocks.append(Block("table", caption_text + "\n".join(" | ".join(row) for row in rows), anchor))
                return
        pending = []
        for child in node.children:
            if isinstance(child, NavigableString):
                pending.append(str(child))
            elif isinstance(child, Tag):
                if child.name in containers or child.find(list(containers)):
                    if pending:
                        emit("".join(pending), node, anchor)
                        pending = []
                    walk(child, anchor)
                elif child.name == "br":
                    pending.append("\n")
                else:
                    # Preserve visible inline XBRL values, punctuation, and spaces.
                    if child.get("id") and not pending:
                        anchor = child["id"]
                    pending.append(child.get_text("", strip=False))
        if pending:
            emit("".join(pending), node, anchor)

    walk(soup.body or soup)
    part, item, subsection = "", "Cover", ""
    cleaned = []
    for block in blocks:
        if block.kind == "heading":
            if re.match(r"^PART\s+[IVX]+\b", block.text, re.I):
                part, item, subsection = block.text, "", ""
            elif re.match(r"^ITEM\s+\d", block.text, re.I):
                item, subsection = block.text, ""
            else:
                subsection = block.text
        block.section = " / ".join(s for s in (part, item, subsection) if s)
        # Do not deduplicate repeated disclosures across reporting periods.
        block.block_id = f"b{len(cleaned):06d}"
        cleaned.append(block)
    if not cleaned:
        raise ValueError("HTML extraction produced no content")
    return cleaned


def windows(text, size, overlap=0):
    """Exact token windows with Unicode-safe character offsets, no replacement text."""
    ids = tokens(text)
    _, offsets = tokenizer().decode_with_offsets(ids)
    offsets.append(len(text))
    start = 0
    while start < len(ids):
        end = min(start + size, len(ids))
        while end > start and end < len(ids) and offsets[end] == offsets[end - 1]:
            end -= 1
        if end <= start:
            raise ValueError("Token window too small for Unicode character")
        left, right = offsets[start], offsets[end]
        yield text[left:right], left, right
        if end == len(ids):
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and offsets[next_start] == offsets[next_start - 1]:
            next_start += 1
        start = next_start


def narrative_units(text, max_tokens=120):
    units = []
    cursor = 0
    for sentence in re.split(r"(?<=[.!?;])\s+(?=[A-Z(\d])|\n\n+", text):
        start = text.find(sentence, cursor)
        cursor = start + len(sentence)
        if sentence.strip():
            units.extend((part, start + left, start + right) for part, left, right in windows(sentence, max_tokens))
    return units


def semantic_groups(units, vectors, min_tokens, target_tokens, max_tokens, overlap, percentile):
    if not units:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)
    if len(matrix) != len(units) or not np.isfinite(matrix).all():
        raise ValueError("Invalid semantic unit embeddings")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Zero semantic unit embedding")
    matrix = matrix / norms
    distances = 1 - np.sum(matrix[:-1] * matrix[1:], axis=1)
    threshold = float(np.percentile(distances, percentile)) if len(distances) else 1.0
    groups, start, end = [], 0, 0
    while end < len(units):
        length = len(tokens(" ".join(u[0] for u in units[start:end + 1])))
        if length > max_tokens and end > start:
            cut = end
        elif end == len(units) - 1:
            groups.append((start, len(units)))
            break
        elif length >= min_tokens and (distances[end] > threshold or length >= target_tokens):
            cut = end + 1
        else:
            end += 1
            continue
        groups.append((start, cut))
        next_start = cut
        while next_start > start + 1 and len(tokens(" ".join(u[0] for u in units[next_start - 1:cut]))) <= overlap:
            next_start -= 1
        start, end = next_start, cut
    return groups


class Embeddings:
    """Content-addressed SQLite cache, bounded requests, retries, strict validation."""
    def __init__(self, cache, model, dimensions, key=None, request=None):
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache)
        self.db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)")
        self.model, self.dimensions, self.key = model, dimensions, key
        self.transport = request or self.request
        self.usage = {"requests": 0, "input_tokens": 0, "cache_hits": 0}

    def close(self):
        self.db.close()

    def request(self, texts):
        body = encode({"model": self.model, "dimensions": self.dimensions, "encoding_format": "float", "input": texts})
        opener = build_opener(NoRedirect())
        for attempt in range(6):
            req = Request("https://api.openai.com/v1/embeddings", data=body, headers={
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=120) as response:
                    return json.load(response)
            except HTTPError as error:
                status = error.code
                retry_after = error.headers.get("Retry-After", "")
                error.close()
                if status not in {429, 500, 502, 503, 504} or attempt == 5:
                    raise RuntimeError(f"OpenAI embeddings HTTP {status}; check key, model access, quota, or request limits. Cached batches are retained.") from None
                delay = min(float(retry_after), 60) if retry_after.isdigit() else min(2 ** attempt + random.random(), 60)
            except (URLError, TimeoutError):
                if attempt == 5:
                    raise RuntimeError("OpenAI network request failed; rerun to reuse cached batches.") from None
                delay = 2 ** attempt
            time.sleep(delay)

    def key_for(self, text):
        return digest([self.model, self.dimensions, text])

    def get_many(self, texts):
        unique = dict.fromkeys(texts)
        found, pending = {}, []
        for text in unique:
            row = self.db.execute("SELECT vector FROM embeddings WHERE key=?", (self.key_for(text),)).fetchone()
            if row:
                vector = np.frombuffer(row[0], dtype="<f4").copy()
                self.validate(vector)
                found[text] = vector
                self.usage["cache_hits"] += 1
            else:
                count = len(tokens(text))
                if not 0 < count <= 8191:
                    raise ValueError(f"Embedding input has {count} tokens; must be 1–8191")
                pending.append((text, count))
        cursor = 0
        while cursor < len(pending):
            batch, count = [], 0
            while cursor < len(pending) and len(batch) < 64:
                text, length = pending[cursor]
                if batch and count + length > 16000:
                    break
                batch.append(text)
                count += length
                cursor += 1
            result = self.transport(batch)
            if result.get("model") != self.model:
                raise ValueError("Embedding response model does not match configured model")
            data = result.get("data", [])
            if len(data) != len(batch) or sorted(d.get("index", -1) for d in data) != list(range(len(batch))):
                raise ValueError("Embedding response has missing or duplicate indices")
            vectors = []
            for item in sorted(data, key=lambda d: d["index"]):
                vector = np.asarray(item["embedding"], dtype="<f4")
                self.validate(vector)
                vectors.append(vector)
            with self.db:
                for text, vector in zip(batch, vectors):
                    self.db.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?)", (self.key_for(text), vector.tobytes()))
                    found[text] = vector
            self.usage["requests"] += 1
            self.usage["input_tokens"] += result.get("usage", {}).get("total_tokens", count)
            print(f"Embedding batches: {self.usage['requests']}; billed input tokens this run: {self.usage['input_tokens']}", flush=True)
        return [found[text] for text in texts]

    def validate(self, vector):
        if vector.shape != (self.dimensions,) or not np.isfinite(vector).all() or not np.any(vector):
            raise ValueError("Embedding response/cache vector has wrong dimensions, nonfinite values, or zero norm")


def parents(blocks):
    """Same section/table boundaries and canonical parent text for both strategies."""
    result, current = [], None
    for block in blocks:
        if block.kind == "table":
            context = current["text"][-600:] if current else ""
            result.append({"kind": "table", "section": block.section, "anchor": block.anchor,
                           "block_ids": [block.block_id], "text": block.text, "context": context})
            current = None
        elif current and current["section"] == block.section:
            current["text"] += "\n\n" + block.text
            current["block_ids"].append(block.block_id)
        else:
            current = {"kind": "narrative", "section": block.section, "anchor": block.anchor,
                       "block_ids": [block.block_id], "text": block.text, "context": ""}
            result.append(current)
    for i, parent in enumerate(result):
        parent["parent_index"] = i
    return result


def table_chunks(parent, budget):
    rows = parent["text"].splitlines()
    # Repeat initial context/header rows. Preserve all rows, including footnotes.
    header_rows = rows[:min(3, max(1, len(rows) - 1))]
    header = parent["context"] + "\n" + "\n".join(header_rows)
    # For unusually wide headers, preserve all table text using token windows
    # rather than silently truncating header values. Canonical table stays intact.
    if len(tokens(header)) > budget // 2:
        return [part for part, _, _ in windows(parent["context"] + "\n" + parent["text"], budget, min(100, budget // 4))]
    groups, current = [], header
    for row in rows[len(header_rows):]:
        for piece, _, _ in windows(row, max(32, budget - len(tokens(header)) - 10)):
            if len(tokens(current + "\n" + piece)) > budget and current != header:
                groups.append(current)
                current = header
            current += "\n" + piece
    if current.strip():
        groups.append(current)
    return groups


def prefix_for(document, parent):
    return (f"{document['company']} | {document['ticker']} | {document['form']} | "
            f"Report period end {document['reportDate']}\n{parent['section']}\n")


def build_chunks(document, source_parents, strategy, settings, embedder=None):
    records = []
    for parent in source_parents:
        prefix = prefix_for(document, parent)
        parent_id = f"{document['cik']}:{document['accession']}:p{parent['parent_index']:05d}"
        prefix_size = len(tokens(prefix))
        maximum = settings["max_tokens"] - prefix_size - 8
        target = min(settings["target_tokens"] - prefix_size - 8, maximum)
        if target <= settings["overlap"] or maximum < 150:
            raise ValueError("Section prefix leaves too little room for chunk content")
        if parent["kind"] == "table":
            pieces = [(text, -1, -1) for text in table_chunks(parent, target)]
        elif strategy == "fixed":
            pieces = list(windows(parent["text"], target, settings["overlap"]))
        elif len(tokens(parent["text"])) <= target:
            # No boundary choice to make: avoid embedding sentence units for
            # short parents that already fit in a single retrieval chunk.
            pieces = [(parent["text"], 0, len(parent["text"]))]
        else:
            if embedder is None:
                raise ValueError("Semantic chunking requires real unit embeddings")
            units = narrative_units(parent["text"])
            unit_inputs = [prefix + unit[0] for unit in units]
            vectors = embedder.get_many(unit_inputs)
            groups = semantic_groups(units, vectors, settings["min_tokens"], target, maximum,
                                     settings["overlap"], settings["percentile"])
            pieces = [(parent["text"][units[a][1]:units[b - 1][2]], units[a][1], units[b - 1][2]) for a, b in groups]
        for i, (text, start, end) in enumerate(pieces):
            embedding_text = prefix + text
            if len(tokens(embedding_text)) > settings["max_tokens"]:
                raise ValueError("Chunk exceeds configured maximum input tokens")
            meta = {"company": document["company"], "ticker": document["ticker"], "cik": document["cik"],
                    "accession": document["accession"], "form": document["form"],
                    "report_date": document["reportDate"], "filing_date": document["filingDate"],
                    "source_url": document["url"], "source_anchor": parent["anchor"],
                    "source_sha256": document["sha256"], "section_path": parent["section"],
                    "parent_id": parent_id, "chunk_type": parent["kind"], "strategy": strategy,
                    "parser_version": VERSION, "pipeline_fingerprint": settings["fingerprint"],
                    "embedding_model": settings["model"], "embedding_dimensions": settings["dimensions"],
                    "text": embedding_text, "token_count": len(tokens(embedding_text)),
                    "char_start": start, "char_end": end}
            if len(encode(meta)) > 39000:
                raise ValueError("Chunk metadata too large for Pinecone")
            identifier = f"{parent_id}:{strategy}:{settings['fingerprint'][:12]}:{i:05d}"
            records.append({"id": identifier, "metadata": meta})
    return records


def embed_records(records, embedder):
    for start in range(0, len(records), 128):
        batch = records[start:start + 128]
        vectors = embedder.get_many([r["metadata"]["text"] for r in batch])
        for record, vector in zip(batch, vectors):
            yield {"id": record["id"], "values": vector.tolist(), "metadata": record["metadata"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embed", action="store_true", help="Make paid OpenAI calls; produce both embedding files")
    parser.add_argument("--strategy", choices=["both", "fixed", "semantic"], default="both")
    parser.add_argument("--target-tokens", type=int, default=700)
    parser.add_argument("--min-tokens", type=int, default=350)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=100)
    parser.add_argument("--percentile", type=float, default=80, help="Semantic distance boundary percentile within each parent")
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    if not (0 <= args.overlap < args.min_tokens <= args.target_tokens <= args.max_tokens <= 2000) or not 0 < args.percentile < 100:
        parser.error("Require 0 <= overlap < min <= target <= max <= 2000 and 0 < percentile < 100")
    if args.strategy == "semantic" and not args.embed:
        parser.error("Semantic chunking requires --embed; no lexical fallback is used")
    config = json.loads((ROOT / "pinecone/index.json").read_text())
    catalog = json.loads((ROOT / "data/filings.json").read_text())
    manifest = json.loads((ROOT / "data/filings/manifest.json").read_text())
    documents = [d for d in manifest["entries"] if d["ticker"] in TICKERS]
    expected_count = len(TICKERS) * EXPERIMENT["periods_per_company"]
    if len(documents) != expected_count or Counter(d["ticker"] for d in documents) != Counter({t: EXPERIMENT["periods_per_company"] for t in TICKERS}):
        raise ValueError(f"Expected {EXPERIMENT['periods_per_company']} downloaded filings for each selected company")
    if catalog["asOf"] != EXPERIMENT["as_of"]:
        raise ValueError("Experiment and catalog cutoffs differ; review experiment.json")
    expected = {(c["tickers"][0], f["accession"], f["url"]) for c in catalog["companies"] for f in c["filings"] if c["tickers"][0] in TICKERS}
    if {(d["ticker"], d["accession"], d["url"]) for d in documents} != expected:
        raise ValueError("Downloaded manifest differs from current selected catalog")
    settings = {"target_tokens": args.target_tokens, "min_tokens": args.min_tokens, "max_tokens": args.max_tokens,
                "overlap": args.overlap, "percentile": args.percentile, "parser": VERSION,
                "model": config["tags"]["embedding-model"], "dimensions": config["dimension"], "experiment": EXPERIMENT}
    # Validate every source checksum before creating outputs or making embedding calls.
    for doc in documents:
        path = (ROOT / doc["localPath"]).resolve()
        if not path.is_relative_to((ROOT / "data/filings").resolve()) or digest(path.read_bytes()) != doc["sha256"]:
            raise ValueError(f"Source path/checksum failed: {doc['ticker']} {doc['accession']}")
    settings["fingerprint"] = digest([settings, [(d["accession"], d["sha256"]) for d in documents]])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / ".cache/tiktoken"))
    tokenizer()
    embedder = None
    if args.embed:
        key = os.environ.get("OPENAI_API_KEY") or getpass.getpass("OpenAI API key (paid embedding calls): ")
        if not key.strip():
            raise ValueError("OPENAI_API_KEY is required")
        embedder = Embeddings(ROOT / ".cache/embeddings.sqlite3", settings["model"], settings["dimensions"], key)
    strategies = ["fixed", "semantic"] if args.strategy == "both" else [args.strategy]
    if not args.embed:
        strategies = ["fixed"]
    collected = {strategy: [] for strategy in strategies}
    stats = []
    try:
        for number, doc in enumerate(documents, 1):
            print(f"Parsing {number}/{expected_count}: {doc['ticker']} {doc['form']} {doc['reportDate']}", flush=True)
            blocks = extract_html((ROOT / doc["localPath"]).read_text(encoding="utf-8"))
            source_parents = parents(blocks)
            stem = f"{doc['ticker']}/{doc['accession']}"
            write_jsonl(output / "parsed" / f"{stem}.jsonl", [vars(block) for block in blocks])
            write_jsonl(output / "parents" / f"{stem}.jsonl", source_parents)
            counts = {}
            if "semantic" in strategies:
                # Prefetch unit vectors across a document to avoid one HTTP
                # request per subsection; build_chunks then reads the cache.
                unit_inputs = []
                for parent in source_parents:
                    prefix = prefix_for(doc, parent)
                    target = settings["target_tokens"] - len(tokens(prefix)) - 8
                    if parent["kind"] == "narrative" and len(tokens(parent["text"])) > target:
                        unit_inputs.extend(prefix + u[0] for u in narrative_units(parent["text"]))
                embedder.get_many(unit_inputs)
            for strategy in strategies:
                chunks = build_chunks(doc, source_parents, strategy, settings, embedder)
                collected[strategy].extend(chunks)
                counts[strategy] = len(chunks)
            stats.append({"ticker": doc["ticker"], "accession": doc["accession"], "blocks": len(blocks),
                          "tables": sum(b.kind == "table" for b in blocks), "parents": len(source_parents),
                          "extracted_tokens": sum(len(tokens(b.text)) for b in blocks), "chunks": counts})
        for strategy, records in collected.items():
            write_jsonl(output / "chunks" / f"{strategy}.jsonl", records)
            if args.embed:
                path = output / "embeddings" / f"{strategy}.jsonl"
                # Atomic replacement: failed calls retain prior completed exports.
                write_jsonl(path, embed_records(records, embedder))
                read_records(path, config, strategy, catalog)
            print(f"{strategy}: {len(records)} chunks; {sum(r['metadata']['token_count'] for r in records)} final embedding input tokens", flush=True)
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "settings": settings,
                  "mode": "embedded" if args.embed else "offline-preparation", "documents": stats,
                  "strategies": strategies, "usage_this_run": embedder.usage if embedder else {},
                  "semantic_pending": not args.embed and args.strategy == "both",
                  "notes": ["Shared table handling; semantic comparison concerns narrative boundaries.",
                            "Token counts include repeated prefixes/overlap; semantic unit embedding calls add preprocessing cost.",
                            "Heuristic section/table extraction requires manual review; no claim of benchmark-optimal settings."]}
        write_json(output / "pipeline-report.json", report)
        print(f"Completed. Report: {output / 'pipeline-report.json'}", flush=True)
        if not args.embed:
            print("Offline mode: no embeddings generated. Run with --embed to generate semantic chunks and both Pinecone files.")
    finally:
        if embedder:
            embedder.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
