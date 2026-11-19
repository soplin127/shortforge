import pytest

from core.report import CsvReport, ReportRow, free_report_path


def test_free_report_path_skips_existing(tmp_path):
    first = free_report_path(tmp_path, "20261119_120000")
    CsvReport(first).add(ReportRow(source="a.mp4", status="done"))
    second = free_report_path(tmp_path, "20261119_120000")
    CsvReport(second)
    assert second.name == "report_20261119_120000_2.csv"
    assert "a.mp4" in first.read_text(encoding="utf-8-sig")


def test_report_never_truncates_existing_file(tmp_path):
    path = tmp_path / "report.csv"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        CsvReport(path)
    assert path.read_text(encoding="utf-8") == "keep"
