import json
from io import BytesIO

import pytest
from pypdf import PdfWriter

from finwhiz.config import ROOT, AppError, digest
from finwhiz.documents import (
    extract_html,
    make_parents,
    split_parent,
    parse_file,
    token_count,
    windows,
)
from finwhiz.finance import calculate, supports_number
from finwhiz.retrieval import bm25, fuse
from finwhiz.storage import Storage

BASELINE = ROOT / "specs/001-python-backend/baseline"


def test_frozen_domain():
    baseline = json.loads((BASELINE / "domain.json").read_text())
    blocks = extract_html(baseline["html"])
    assert blocks == baseline["blocks"]
    parents = make_parents(blocks)
    assert parents == baseline["parents"]
    for parent, expected in zip(parents, baseline["chunks"]):
        assert split_parent(parent, "fixed") == expected["fixed"]
        assert (
            split_parent(parent, "semantic", lambda texts: [[1, 0] for _ in texts])
            == expected["semantic"]
        )
    assert bm25(baseline["rows"], "capital expenditure") == baseline["bm25"]
    assert (
        fuse([[baseline["rows"][0], baseline["rows"][1]], [baseline["rows"][1]]])
        == baseline["fused"]
    )


def test_legacy_fingerprints(settings):
    for item in json.loads((BASELINE / "embedding-fingerprints.json").read_text()):
        config = settings.provider(item["config"]["provider"])
        assert config.model_dump() == item["config"]
        assert config.fingerprint == item["fingerprint"]


def test_legacy_storage(tmp_path):
    store = Storage(tmp_path)
    store.put("embedding/legacy", "[0.25,-0.5]")
    assert store.json("embedding/legacy") == [0.25, -0.5]
    store.put_json("embedding/new", [0.25, -0.5])
    assert store.get("embedding/new")[:2] == b"\x1f\x8b"
    assert store.json("embedding/new") == [0.25, -0.5]
    assert store.path("state").name == digest("state")
    assert store.json("missing", []) == []


def test_unicode_semantic_tables_and_signed_arithmetic():
    text = (
        "Revenue grew substantially in this reporting period. " * 120
        + "Regulatory litigation creates material operational risks. " * 120
    )
    parent = dict(kind="narrative", text=text)
    calls = []

    def embed(texts):
        calls.extend(texts)
        return [[0, 1] if text.startswith("Regulatory") else [1, 0] for text in texts]

    assert all(token_count(piece) <= 1000 for piece in split_parent(parent, "semantic", embed))
    assert calls
    assert "\ufffd" not in "".join(windows("你好🙂 <|endoftext|> " * 100, 30, 5))
    assert calculate("sum", [100, -20]) == 80
    assert calculate("percent_change", [125, 100]) == 25
    assert supports_number("Loss (200)", -200)
    assert not supports_number("Loss (200)", 200)
    with pytest.raises(AppError):
        calculate("ratio", [1, 0])


def readable_pdf():
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    text = "Ticker: ACME 10-Q Fiscal period end: 2025-03-31. Revenue was 125 million. Operating cash flow was 100 million for the fiscal reporting period."
    stream.set_data(f"BT /F1 10 Tf 20 700 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_readable_pdf_and_scanned_rejection(tmp_path):
    store = Storage(tmp_path)
    file = dict(id="pdf", name="filing.pdf", key="upload/pdf", details={})
    store.put(file["key"], readable_pdf())
    doc = parse_file(file, store)
    assert doc["ticker"] == "ACME" and doc["reportDate"] == "2025-03-31"
    assert "125 million" in doc["blocks"][0]["text"]
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    output = BytesIO()
    writer.write(output)
    store.put(file["key"], output.getvalue())
    file["details"] = dict(ticker="ACME", reportDate="2025-03-31", form="10-Q")
    with pytest.raises(AppError, match="no readable text"):
        parse_file(file, store)
