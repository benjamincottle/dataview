import pytest

from dataview import nfr

HEADER = ["Food or food ingredient", "Outcome View", "Justification/Comment"]


def test_plain_cell_is_whitespace_normalised():
    assert nfr.parse_cell("  Abalone  blood\nextract ") == [("text", "Abalone blood extract")]


def test_number_with_word_anchor_is_a_footnote():
    cell = "requirements in Standard <s>1</s>1.2.3 of the Code.<s>0F</s>"
    assert nfr.parse_cell(cell) == [
        ("text", "requirements in Standard"),
        ("fn", "1"),
        ("text", " 1.2.3 of the Code."),
    ]


def test_number_without_anchor_is_an_exponent():
    cell = "up to 1 x 10<s>9</s>CFU per serve"
    assert nfr.parse_cell(cell) == [
        ("text", "up to 1 x 10"),
        ("sup", "9"),
        ("text", " CFU per serve"),
    ]


def test_trademark_becomes_symbol():
    assert nfr.parse_cell("ClearTaste<s>TM</s>2016") == [("text", "ClearTaste™ 2016")]
    assert nfr.parse_cell("(Phase 2)<s>TM</s>(from beans)") == [("text", "(Phase 2)™ (from beans)")]


def test_misflagged_body_text_is_kept_as_text():
    cell = "fermented and heat-<s>treated grape pomace</s>.<s>2025</s>"
    assert nfr.parse_cell(cell) == [("text", "fermented and heat-treated grape pomace. 2025")]


def test_markup_in_cells_is_left_as_text():
    cell = '<img src=x onerror="alert(1)"> Ginseng'
    assert nfr.parse_cell(cell) == [("text", cell)]


def test_outcome_tone():
    assert nfr.outcome_tone("Novel food") == "novel"
    assert nfr.outcome_tone("novel food") == "novel"
    assert nfr.outcome_tone("Not novel food, except for infants") == "not-novel"
    assert nfr.outcome_tone("Regulate as a food additive (flavour)") == "additive"
    assert nfr.outcome_tone("Traditional food") == "other"


def test_parse_tables_skips_headers_and_splits_outcomes():
    rows = nfr.parse_tables(
        [(3, [HEADER, ["Abalone 2015", "• Non-traditional food • Not novel food", "Ok."]])]
    )
    assert len(rows) == 1
    assert rows[0]["food"] == [("text", "Abalone 2015")]
    assert [o["text"] for o in rows[0]["outcome"]] == ["Non-traditional food", "Not novel food"]
    assert [o["tone"] for o in rows[0]["outcome"]] == ["other", "not-novel"]
    assert rows[0]["page"] == 3


def test_row_split_across_pages_is_merged():
    rows = nfr.parse_tables(
        [
            (6, [HEADER, ["Bacillus subtilis", "• Not novel food", "Safe at intended levels"]]),
            (7, [HEADER, ["", "", "of use."], ["Barley", "• Traditional food", "Ok."]]),
        ]
    )
    assert len(rows) == 2
    assert rows[0]["justification"] == [("text", "Safe at intended levels of use.")]
    assert rows[1]["food"] == [("text", "Barley")]


def test_cross_reference_row_mid_table_is_not_merged():
    rows = nfr.parse_tables(
        [
            (1, [HEADER, ["Apple", "• Novel food", "No."]]),
            (2, [HEADER, ["Banana", "• Not novel food", "Yes."], ["Plantain – see Banana", "", ""]]),
        ]
    )
    assert [nfr.plain_text(r["food"]) for r in rows] == ["Apple", "Banana", "Plantain – see Banana"]
    assert rows[2]["outcome"] == []


def test_rows_with_unexpected_shape_are_skipped():
    rows = nfr.parse_tables([(1, [HEADER, ["only", "two"], ["Apple", "• Novel food", "No."]])])
    assert len(rows) == 1


def test_no_rows_is_an_error():
    with pytest.raises(nfr.ParseError):
        nfr.parse_tables([(1, [HEADER])])


def test_footnotes_are_attached_from_page_text():
    rows = nfr.parse_tables([(28, [HEADER, ["Hovenia dulcis 2021<s>8F</s><s>9</s>", "• Novel food", "No."]])])
    page_text = (
        "Hovenia dulcis\r\n2 x 10 cells per serve\r\n"
        "9 This item was considered further in 2021, with a view that\r\n"
        "the ingredient may be more appropriately regulated.\r\n"
        "10 Another note."
    )
    nfr.attach_footnotes(rows, {28: page_text})
    assert rows[0]["notes"] == [
        {
            "n": "9",
            "page": 28,
            "text": "This item was considered further in 2021, with a view that "
            "the ingredient may be more appropriately regulated.",
        }
    ]
    assert "refs" not in rows[0]


def test_missing_footnote_text_is_none():
    rows = nfr.parse_tables([(5, [HEADER, ["Kelp<s>1F</s><s>2</s>", "• Novel food", "No."]])])
    nfr.attach_footnotes(rows, {})
    assert rows[0]["notes"] == [{"n": "2", "page": 5, "text": None}]


def test_find_pdf_url_resolves_relative_links():
    html = b'<a class="file-download file-download-pdf" href="/sites/files/Record%20of%20Views.pdf">x</a>'
    assert nfr.find_pdf_url(html) == "https://www.foodstandards.gov.au/sites/files/Record%20of%20Views.pdf"


@pytest.mark.parametrize(
    "html",
    [
        b"<p>No link here</p>",
        b'<a class="file-download-pdf" href="https://evil.example/x.pdf">x</a>',
        b'<a class="file-download-pdf" href="http://www.foodstandards.gov.au/x.pdf">x</a>',
    ],
)
def test_find_pdf_url_rejects_missing_or_foreign_links(html):
    with pytest.raises(nfr.UpstreamError):
        nfr.find_pdf_url(html)


def test_title_from_url():
    url = "https://www.foodstandards.gov.au/sites/2026-07/Record%20of%20Views%20updated%20July%202026.pdf"
    assert nfr.title_from_url(url) == "Record of Views updated July 2026"
