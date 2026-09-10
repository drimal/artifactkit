import base64

import pytest

from artifactkit.core.errors import ArtifactError
from artifactkit.core.parsing import (
    parse_document_spec,
    parse_file_bundle_spec,
    parse_presentation_spec,
    parse_workbook_spec,
)


def test_parse_document_spec_all_block_types():
    spec = parse_document_spec({
        "title": "Report",
        "blocks": [
            {"type": "heading", "text": "Intro", "level": 2},
            {"type": "paragraph", "text": "Hello"},
            {"type": "paragraph", "runs": [{"text": "bold", "style": "bold"}]},
            {"type": "list", "items": ["a", "b"], "ordered": True},
            {"type": "table", "headers": ["X", "Y"], "rows": [["1", "2"]]},
            {"type": "image", "source_path": "/tmp/x.png", "caption": "fig 1"},
            {"type": "page_break"},
        ],
        "base_filename": "report",
    })
    assert spec.title == "Report"
    assert len(spec.blocks) == 7
    assert spec.base_filename == "report"


def test_parse_document_spec_unknown_block_type_raises():
    with pytest.raises(ArtifactError, match="unknown block type"):
        parse_document_spec({"title": "x", "blocks": [{"type": "not_a_real_type"}]})


def test_parse_document_spec_missing_field_raises():
    with pytest.raises(ArtifactError, match="missing required field"):
        parse_document_spec({"blocks": []})


def test_parse_presentation_spec():
    spec = parse_presentation_spec({
        "title": "Deck",
        "slides": [
            {"layout": "title", "placeholders": {"title": "Hi"}},
            {
                "layout": "title_and_body",
                "placeholders": {"title": "A", "body": "B"},
                "speaker_notes": "remember to smile",
            },
        ],
    })
    assert len(spec.slides) == 2
    assert spec.slides[1].speaker_notes == "remember to smile"


def test_parse_workbook_spec_with_formulas():
    spec = parse_workbook_spec({
        "sheets": [
            {
                "name": "Data",
                "header": ["A", "B", "Total"],
                "rows": [["1", "2", None]],
                "formulas": {"C2": "=A2+B2"},
            }
        ]
    })
    assert spec.sheets[0].formulas == {"C2": "=A2+B2"}


def test_parse_file_bundle_spec_with_content_base64():
    payload = base64.b64encode(b"hello").decode()
    spec = parse_file_bundle_spec({"files": [{"filename": "a.txt", "content_base64": payload}]})
    assert spec.files[0].content == b"hello"
    assert spec.files[0].source_path is None


def test_parse_file_bundle_spec_with_source_path():
    spec = parse_file_bundle_spec({"files": [{"filename": "a.txt", "source_path": "/tmp/a.txt"}]})
    assert spec.files[0].source_path == "/tmp/a.txt"
    assert spec.files[0].content is None


def test_parse_file_bundle_spec_rejects_both_content_and_path():
    with pytest.raises(ArtifactError, match="exactly one of"):
        parse_file_bundle_spec({
            "files": [{"filename": "a.txt", "content_base64": "aGk=", "source_path": "/tmp/a.txt"}]
        })


def test_parse_file_bundle_spec_rejects_neither():
    with pytest.raises(ArtifactError, match="exactly one of"):
        parse_file_bundle_spec({"files": [{"filename": "a.txt"}]})


def test_parse_file_bundle_spec_invalid_base64_raises_artifact_error():
    with pytest.raises(ArtifactError, match="invalid base64"):
        parse_file_bundle_spec({"files": [{"filename": "a.txt", "content_base64": "not-valid-base64!!"}]})


def test_parse_file_bundle_spec_bundle_filename_passthrough():
    payload = base64.b64encode(b"x").decode()
    spec = parse_file_bundle_spec({
        "files": [{"filename": "a.txt", "content_base64": payload}],
        "bundle_filename": "archive",
    })
    assert spec.bundle_filename == "archive"
