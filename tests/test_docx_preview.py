import io
import zipfile

import pytest

from exo.docx_preview import MAX_FILE, DocumentError, docx_markdown

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def package(body, extra=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        for name, data in extra:
            archive.writestr(name, data)
    return stream.getvalue()


def p(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_extracts_paragraphs_and_table_with_source_locations():
    data = package(p("Specification") + "<w:tbl><w:tr><w:tc>" + p("Name") + "</w:tc><w:tc>" +
                   p("Value") + "</w:tc></w:tr><w:tr><w:tc>" + p("a") + "</w:tc><w:tc>" +
                   p("b") + "</w:tc></w:tr></w:tbl>")
    result = docx_markdown(data)
    assert "Specification" in result["markdown"]
    assert "| Name | Value |\n| --- | --- |\n| a | b |" in result["markdown"]
    assert result["blocks"][2]["location"] == {"table": 1, "row": 2}
    assert not result["truncated"] and len(result["source_sha256"]) == 64


def test_never_fetches_external_relationship_or_executes_embedded_content():
    data = package('<w:p><w:hyperlink><w:r><w:t>visible label</w:t></w:r></w:hyperlink></w:p>',
                   [("word/_rels/document.xml.rels", '<Relationship Target="https://example.invalid/x"/>'),
                    ("word/embeddings/code.bin", "raise RuntimeError('never execute')")])
    result = docx_markdown(data)
    assert result["markdown"] == "visible label"
    assert "example.invalid" not in result["markdown"]
    assert any("not fetched" in w for w in result["warnings"])


def test_output_bounded_and_truncation_explicit():
    result = docx_markdown(package(p("x" * 1000)), max_output=256)
    assert len(result["markdown"]) == 256 and result["truncated"]
    assert any("truncated" in w for w in result["warnings"])


def test_html_and_markdown_text_escaped():
    result = docx_markdown(package(p("&lt;script&gt; *bold* | [link]")))
    assert "<script>" not in result["markdown"] and r"\*bold\*" in result["markdown"]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "word/../../escape", "word\\escape"])
def test_archive_path_escape_rejected(name):
    with pytest.raises(DocumentError):
        docx_markdown(package(p("ok"), [(name, "never extracted")]))


def test_duplicate_members_and_symlinks_rejected():
    with pytest.warns(UserWarning):
        data = package(p("ok"), [("word/document.xml", "other")])
    with pytest.raises(DocumentError):
        docx_markdown(data)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("word/document.xml")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(DocumentError):
        docx_markdown(stream.getvalue())


def test_bomb_and_oversized_inputs_refused():
    with pytest.raises(DocumentError):
        docx_markdown(b"x" * (MAX_FILE + 1))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 1_000_000)
    with pytest.raises(DocumentError, match="expansion"):
        docx_markdown(stream.getvalue())


def test_dtd_entities_and_malformed_xml_rejected():
    for xml in ('<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>', '<broken', '<x/>'):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", xml)
        with pytest.raises(DocumentError):
            docx_markdown(stream.getvalue())
    with pytest.raises(DocumentError):
        docx_markdown(b"not a zip")


def test_cancellation_stops_at_checkpoint():
    checks = []
    def cancelled():
        checks.append(1)
        return len(checks) > 4
    with pytest.raises(DocumentError, match="cancelled"):
        docx_markdown(package(p("a") * 20), cancelled=cancelled)
    assert len(checks) == 5


def test_image_only_and_tracked_changes_reported():
    result = docx_markdown(package('<w:p><w:ins><w:r><w:t>unsettled</w:t></w:r></w:ins></w:p>'))
    assert "unsettled" not in result["markdown"]
    assert any("Tracked" in w for w in result["warnings"])
    assert any("No supported text" in w for w in result["warnings"])


def test_member_count_and_table_column_caps():
    with pytest.raises(DocumentError):
        docx_markdown(package(p("ok"), [(f"extra/{i}", "") for i in range(129)]))
    with pytest.raises(DocumentError, match="column"):
        docx_markdown(package("<w:tbl><w:tr>" + ("<w:tc>" + p("x") + "</w:tc>") * 33 + "</w:tr></w:tbl>"))


def test_encryption_flag_is_refused_before_read():
    import struct
    data = bytearray(package(p("text")))
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<H", data, central + 8, 1)
    struct.pack_into("<H", data, 6, 1)
    with pytest.raises(DocumentError, match="encrypted"):
        docx_markdown(bytes(data))
