import pytest

from artifactkit import (
    DocumentSpec,
    FileBundleSpec,
    Heading,
    Paragraph,
    RawFile,
    Sheet,
    Table,
    WorkbookSpec,
)


def test_document_spec_requires_title_and_blocks():
    with pytest.raises(ValueError):
        DocumentSpec(title="", blocks=(Paragraph.of("x"),))
    with pytest.raises(ValueError):
        DocumentSpec(title="x", blocks=())


def test_base_filename_rejects_extension():
    with pytest.raises(ValueError, match="no extension"):
        DocumentSpec(title="x", blocks=(Paragraph.of("x"),), base_filename="report.docx")


def test_base_filename_rejects_path_separators():
    with pytest.raises(ValueError, match="path separators"):
        DocumentSpec(title="x", blocks=(Paragraph.of("x"),), base_filename="sub/dir")


def test_table_row_width_must_match_headers():
    with pytest.raises(ValueError):
        Table(headers=("a", "b"), rows=(("1", "2", "3"),))


def test_raw_file_requires_exactly_one_of_content_or_source_path():
    with pytest.raises(ValueError):
        RawFile("x.txt")  # neither
    with pytest.raises(ValueError):
        RawFile("x.txt", content=b"a", source_path="/tmp/a.txt")  # both


def test_raw_file_rejects_path_separators():
    with pytest.raises(ValueError, match="path separators"):
        RawFile("../evil.txt", content=b"x")


def test_raw_file_content_must_be_bytes():
    with pytest.raises(TypeError):
        RawFile("x.txt", content="not bytes")


def test_file_bundle_spec_rejects_duplicate_filenames():
    with pytest.raises(ValueError, match="duplicate filenames"):
        FileBundleSpec(files=(RawFile("a.txt", content=b"1"), RawFile("a.txt", content=b"2")))


def test_file_bundle_spec_requires_at_least_one_file():
    with pytest.raises(ValueError):
        FileBundleSpec(files=())


def test_workbook_spec_rejects_duplicate_sheet_names():
    with pytest.raises(ValueError, match="duplicate sheet"):
        WorkbookSpec(sheets=(Sheet(name="Data"), Sheet(name="Data")))


def test_heading_level_must_be_1_to_4():
    with pytest.raises(ValueError):
        Heading("x", level=5)
