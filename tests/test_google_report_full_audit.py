"""Full audit report generation from non-Google audit data."""

from __future__ import annotations

import builtins
import os
import re
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import google_report  # noqa: E402


def test_module_import_is_safe_without_native_report_dependencies() -> None:
    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"matplotlib", "weasyprint"}:
            raise ImportError(f"{name} unavailable")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=unavailable):
        namespace = runpy.run_path(str(Path(google_report.__file__)))

    assert namespace["plt"] is None
    assert namespace["HTML"] is None


def test_html_report_without_chart_data_does_not_require_native_report_dependencies(
    tmp_path: Path,
) -> None:
    with patch.object(google_report, "plt", None), \
         patch.object(google_report, "np", None), \
         patch.object(google_report, "HTML", None), \
         patch.object(google_report, "_CHART_IMPORT_ERROR", ImportError("missing")):
        result = google_report.generate_report(
            "full",
            {"summary": {"health_score": 80}},
            "example.com",
            tmp_path,
            output_format="html",
        )

    assert result["error"] is None
    assert Path(result["files"][0]).is_file()


def test_chart_report_returns_dependency_error_at_runtime(tmp_path: Path) -> None:
    with patch.object(google_report, "plt", None), \
         patch.object(google_report, "np", None), \
         patch.object(google_report, "_CHART_IMPORT_ERROR", ImportError("missing")):
        result = google_report.generate_report(
            "cwv-audit",
            {"lighthouse_scores": {"performance": 90}},
            "example.com",
            tmp_path,
            output_format="html",
        )

    assert result["files"] == []
    assert "matplotlib and numpy are required" in result["error"]


def test_pdf_report_returns_dependency_error_at_runtime(tmp_path: Path) -> None:
    with patch.object(google_report, "HTML", None), \
         patch.object(google_report, "_PDF_IMPORT_ERROR", ImportError("missing")):
        result = google_report.generate_report(
            "full",
            {"summary": {"health_score": 80}},
            "example.com",
            tmp_path,
            output_format="pdf",
        )

    assert result["files"] == []
    assert "weasyprint is required" in result["error"]


def test_full_audit_html_includes_summary_categories_and_roadmap(tmp_path: Path) -> None:
    data = {
        "summary": {
            "health_score": 82,
            "business_type": "SaaS",
            "top_findings": [
                {"title": "Canonical mismatch", "severity": "Critical"},
                "Thin service pages",
            ],
            "quick_wins": ["Add missing meta descriptions"],
        },
        "categories": [
            {
                "name": "Technical SEO",
                "score": 74,
                "what_works": ["HTTPS is enabled", "Robots.txt is reachable"],
                "findings": [
                    {
                        "title": "Canonical mismatch",
                        "severity": "Critical",
                        "description": "Homepage canonical points to a staging URL.",
                        "recommendation": "Set canonical to the production HTTPS URL.",
                    }
                ],
            },
            {
                "name": "Content Quality",
                "score": 68,
                "what_works": ["Clear product positioning"],
                "findings": [
                    {
                        "title": "Thin comparison pages",
                        "severity": "High",
                        "description": "Several pages have fewer than 300 words.",
                    }
                ],
            },
        ],
        "action_plan": {
            "phases": [
                {
                    "name": "Phase 1: Indexing Fixes",
                    "timeframe": "Week 1",
                    "items": ["Fix canonical mismatch", "Resubmit sitemap"],
                },
                {
                    "name": "Phase 2: Content Expansion",
                    "timeframe": "Weeks 2-3",
                    "items": ["Expand comparison page copy"],
                },
            ]
        },
    }

    result = google_report.generate_report(
        "full",
        data,
        "example.com",
        tmp_path,
        output_format="html",
    )

    assert result["error"] is None
    html_path = Path(result["files"][0])
    html = html_path.read_text(encoding="utf-8")
    assert "Executive Summary" in html
    assert "SaaS" in html
    assert "Technical SEO" in html
    assert "What Works" in html
    assert "Canonical mismatch" in html
    assert "Action Plan" in html
    assert "Phase 1: Indexing Fixes" in html
    assert "Content Quality" in html


# --- unsevered ("Info"-prefix) findings -------------------------------------
#
# summary.top_findings and category.findings are documented as plain arrays
# (seo-audit/SKILL.md), so the common case is a list of strings. Both
# _build_executive_summary and _build_full_audit_categories must render a
# plain-string finding without inventing an "Info" severity, while a dict
# finding that carries an explicit severity keeps its prefix/badge.


def test_executive_summary_plain_string_finding_has_no_info_prefix() -> None:
    data = {
        "summary": {
            "health_score": 70,
            "top_findings": ["Thin service pages"],
        }
    }

    html = google_report._build_executive_summary("example.com", "2026-01-01", data, "full")

    assert "Thin service pages" in html
    assert "Info:" not in html


def test_executive_summary_dict_finding_keeps_explicit_severity() -> None:
    data = {
        "summary": {
            "health_score": 70,
            "top_findings": [{"title": "Canonical mismatch", "severity": "Critical"}],
        }
    }

    html = google_report._build_executive_summary("example.com", "2026-01-01", data, "full")

    assert "<strong>Critical:</strong> Canonical mismatch" in html


def test_full_audit_categories_plain_string_finding_has_no_info_badge() -> None:
    data = {
        "categories": [
            {
                "name": "Technical SEO",
                "findings": ["Thin service pages"],
            }
        ]
    }

    html = google_report._build_full_audit_categories(data)

    assert "<h4>Thin service pages</h4>" in html
    assert "Info" not in html


def test_full_audit_categories_dict_finding_keeps_explicit_severity() -> None:
    data = {
        "categories": [
            {
                "name": "Technical SEO",
                "findings": [{"title": "Canonical mismatch", "severity": "Critical"}],
            }
        ]
    }

    html = google_report._build_full_audit_categories(data)

    assert '<h4>Canonical mismatch <span class="status-warn">Critical</span></h4>' in html


# --- Data Sources & Methodology ---------------------------------------------
#
# The methodology table must list only sources whose data is in the report.
# A seo-audit envelope with no psi/crux/gsc/inspection keys used to claim all
# five Google APIs, which misrepresents the audit to clients.

_GOOGLE_API_NAMES = (
    "PageSpeed Insights API",
    "Chrome UX Report (CrUX)",
    "CrUX History API",
    "Google Search Console",
    "URL Inspection API",
)

_ENVELOPE = {
    "summary": {"health_score": 56, "business_type": "Publisher"},
    "categories": [
        {"name": "Technical SEO", "score": 66, "findings": ["Slow TTFB"]},
        {"name": "Performance (CWV)", "score": 48, "findings": ["LCP 27s (lab)"]},
    ],
    "action_plan": {"phases": [{"name": "Phase 1", "items": ["Enable caching"]}]},
    "artifacts": {"findings_dir": "findings/"},
}


def _source_cells(html: str) -> list[str]:
    """Return the Source column of the methodology table."""
    table = html[html.index("Data Sources &amp; Methodology"):]
    table = table[table.index("<tbody>"):table.index("</tbody>")]
    return re.findall(r"<tr><td>(.*?)</td>", table)


def test_methodology_for_envelope_without_google_data_lists_audit_sources() -> None:
    html = google_report._build_methodology_footer("example.com", "Oct 06, 2026", data=_ENVELOPE)

    sources = _source_cells(html)
    assert sources == ["Site crawl", "Specialist analyses", "Lighthouse (lab)"]
    for name in _GOOGLE_API_NAMES:
        assert name not in sources
    assert "Not used in this audit: PageSpeed Insights API" in html
    assert "Google SEO Intelligence Skill" not in html
    assert "Technical SEO, Performance (CWV)" in html


def test_methodology_omits_lab_lighthouse_without_performance_category() -> None:
    data = {**_ENVELOPE, "categories": [{"name": "Content Quality", "score": 55}]}

    sources = _source_cells(google_report._build_methodology_footer("example.com", "x", data=data))

    assert sources == ["Site crawl", "Specialist analyses"]


def test_methodology_envelope_data_sources_and_note_override_defaults() -> None:
    data = {
        **_ENVELOPE,
        "data_sources": [
            {"name": "Screaming Frog crawl", "description": "412 URLs", "frequency": "One-off"},
            "Manual SERP review",
        ],
        "methodology": "Scores follow the agency rubric v3.",
    }

    html = google_report._build_methodology_footer("example.com", "x", data=data)

    assert _source_cells(html) == ["Screaming Frog crawl", "Manual SERP review"]
    assert "412 URLs" in html
    assert "Scores follow the agency rubric v3." in html
    assert "weighted aggregate" not in html


def test_methodology_lists_only_present_google_sources_after_audit_rows() -> None:
    data = {
        **_ENVELOPE,
        "psi": {"psi": {"mobile": {"lighthouse_scores": {"performance": 40}}}},
        "gsc": {"rows": [{"query": "vault", "clicks": 3, "impressions": 90}]},
        "crux": {"error": "insufficient data"},
    }

    html = google_report._build_methodology_footer("example.com", "x", data=data)

    assert _source_cells(html) == [
        "Site crawl", "Specialist analyses", "Lighthouse (lab)",
        "PageSpeed Insights API", "Google Search Console",
    ]
    assert "Not used in this audit: Chrome UX Report (CrUX), CrUX History API, URL Inspection API." in html


def test_methodology_detects_crux_nested_in_psi_output() -> None:
    data = {"psi": {"psi": {"mobile": {}}, "crux": {"metrics": {"largest_contentful_paint": {}}}}}

    sources = _source_cells(google_report._build_methodology_footer("example.com", "x", data=data))

    assert sources == ["PageSpeed Insights API", "Chrome UX Report (CrUX)"]


def test_methodology_single_source_report_types() -> None:
    footer = google_report._build_methodology_footer

    cwv = _source_cells(footer("e.com", "x", report_type="cwv-audit",
                               data={"lighthouse_scores": {"performance": 90}}))
    gsc = _source_cells(footer("e.com", "x", report_type="gsc-performance", data={"rows": []}))
    idx = _source_cells(footer("e.com", "x", report_type="indexation", data={"total": 3}))

    assert cwv == ["PageSpeed Insights API"]
    assert gsc == ["Google Search Console"]
    assert idx == ["URL Inspection API"]


def test_full_report_html_methodology_matches_envelope(tmp_path: Path) -> None:
    result = google_report.generate_report("full", _ENVELOPE, "example.com", tmp_path,
                                           output_format="html")

    html = Path(result["files"][0]).read_text(encoding="utf-8")
    assert "PageSpeed Insights API</td>" not in html
    assert "<tr><td>Site crawl</td>" in html


# --- PDF review --------------------------------------------------------------


def test_review_pdf_passes_when_optional_pypdf_is_missing(tmp_path: Path) -> None:
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    html = '<div class="section">' + "Plenty of real section text here. " * 5 + "</div>"
    real_import = builtins.__import__

    def no_pypdf(name, *args, **kwargs):
        if name.split(".", 1)[0] == "pypdf":
            raise ImportError("pypdf unavailable")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=no_pypdf):
        review = google_report._review_pdf(str(pdf), html)

    assert review["status"] == "PASS"
    assert review["issues"] == []
    assert review["checks_skipped"] == ["page_count (pypdf not installed)"]


def test_review_pdf_uses_render_page_count_without_pypdf(tmp_path: Path) -> None:
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    html = '<div class="section">' + "Plenty of real section text here. " * 5 + "</div>"

    review = google_report._review_pdf(str(pdf), html, page_count=12)

    assert review["page_count"] == 12
    assert review["checks_skipped"] == []
    assert review["status"] == "PASS"


@pytest.mark.skipif(google_report.HTML is None, reason="weasyprint not installed")
def test_full_audit_pdf_review_reports_page_count(tmp_path: Path) -> None:
    result = google_report.generate_report("full", _ENVELOPE, "example.com", tmp_path,
                                           output_format="pdf")

    assert result["error"] is None
    assert result["review"]["page_count"] >= 1
    assert result["review"]["checks_skipped"] == []
    assert not any("pypdf" in issue for issue in result["review"]["issues"])


# --- Category score chart ----------------------------------------------------


def test_category_chart_skipped_without_matplotlib(tmp_path: Path) -> None:
    with patch.object(google_report, "plt", None):
        assert google_report.chart_category_scores(_ENVELOPE, tmp_path) == ""


def test_category_chart_skipped_without_numeric_scores(tmp_path: Path) -> None:
    data = {"categories": [{"name": "Technical SEO", "score": "n/a"}, "not a dict"]}

    assert google_report.chart_category_scores(data, tmp_path) == ""


@pytest.mark.skipif(google_report.plt is None, reason="matplotlib not installed")
def test_full_audit_report_includes_category_chart_with_caption(tmp_path: Path) -> None:
    result = google_report.generate_report("full", _ENVELOPE, "example.com", tmp_path,
                                           output_format="html")

    assert (tmp_path / "charts" / "category_scores.png").is_file()
    html = Path(result["files"][0]).read_text(encoding="utf-8")
    assert "Figure 1: Score by audit category" in html
    assert 'style="width: 85%;"' in html


@pytest.mark.skipif(google_report.plt is None, reason="matplotlib not installed")
def test_category_chart_shifts_cwv_figure_numbers(tmp_path: Path) -> None:
    data = {
        **_ENVELOPE,
        "psi": {"psi": {"mobile": {"lighthouse_scores": {
            "performance": 40, "accessibility": 90, "best-practices": 80, "seo": 85,
        }}}},
    }

    result = google_report.generate_report("full", data, "example.com", tmp_path,
                                           output_format="html")

    html = Path(result["files"][0]).read_text(encoding="utf-8")
    assert html.count("Figure 1:") == 1
    assert "Figure 2:" in html
