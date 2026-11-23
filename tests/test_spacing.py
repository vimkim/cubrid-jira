import pytest

from cubrid_jira.spacing import find_markup_contacts, normalize_jira_inline_spacing


def test_normalize_jira_code_span_next_to_korean_suffix():
    text = "주 회귀: {{xlocator_fetch_all}}의 값과 전체 {{X'..'}}로 출력"

    assert normalize_jira_inline_spacing(text) == (
        "주 회귀: {{xlocator_fetch_all}} 의 값과 전체 {{X'..'}} 로 출력"
    )


def test_normalize_jira_inline_markup_next_to_korean_prefix_and_suffix():
    text = "한국{{code}}값과 한국*강조*값과 한국_기울임_값"

    assert normalize_jira_inline_spacing(text) == (
        "한국 {{code}} 값과 한국 *강조* 값과 한국 _기울임_ 값"
    )


def test_normalize_skips_jira_code_and_noformat_blocks():
    text = (
        "본문{{code}}값\n"
        "{code:sql}\n"
        "SELECT '본문{{code}}값';\n"
        "{code}\n"
        "{noformat}\n"
        "본문{{code}}값\n"
        "{noformat}\n"
    )

    assert normalize_jira_inline_spacing(text) == (
        "본문 {{code}} 값\n"
        "{code:sql}\n"
        "SELECT '본문{{code}}값';\n"
        "{code}\n"
        "{noformat}\n"
        "본문{{code}}값\n"
        "{noformat}\n"
    )


def test_normalize_skips_markdown_fenced_blocks():
    text = "본문{{code}}값\n```sql\nSELECT '본문{{code}}값';\n```\n"

    assert normalize_jira_inline_spacing(text) == (
        "본문 {{code}} 값\n```sql\nSELECT '본문{{code}}값';\n```\n"
    )


def test_normalize_jira_code_span_nested_in_bold():
    text = "* *{{REC_HOME}}이 아닌 행.* 병합은 *한국{{x}}값*끝"

    assert normalize_jira_inline_spacing(text) == (
        "* *{{REC_HOME}} 이 아닌 행.* 병합은 *한국 {{x}} 값* 끝"
    )


def test_find_contacts_reports_line_column_and_kind():
    from cubrid_jira.spacing import find_markup_contacts

    text = "정상 {{ok}} 값\n* *{{REC_HOME}}이 아닌 행.*\n앞{{x}} 끝\n"

    contacts = find_markup_contacts(text)

    assert [(c.line, c.column, c.marker) for c in contacts] == [
        (2, 14, "}}"),
        (3, 2, "{{"),
    ]
    assert contacts[0].text == "* *{{REC_HOME}}이 아닌 행.*"


def test_find_contacts_skips_code_blocks_and_korean_inside_monospace():
    from cubrid_jira.spacing import find_markup_contacts

    text = (
        "{{한글}} 은 정상\n"
        "{code:sql}\n"
        "SELECT '{{x}}값';\n"
        "{code}\n"
        "```\n"
        "{{x}}값\n"
        "```\n"
    )

    assert find_markup_contacts(text) == []


def test_normalized_output_has_no_contacts():
    from cubrid_jira.spacing import find_markup_contacts

    text = "* *{{REC_HOME}}이 아닌 행.* 한국{{code}}값 _기울임{{y}}값_끝"

    assert find_markup_contacts(normalize_jira_inline_spacing(text)) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("{{a}}b", "{{a}} b"),
        ("b{{x}}.", "b {{x}}."),
        ("{{x}}7", "{{x}} 7"),
        ("7{{x}}", "7 {{x}}"),
        ("{{a}}값", "{{a}} 값"),
        ("값{{a}}", "값 {{a}}"),
        ("a{{x}}b", "a {{x}} b"),
        ("{{a}}7{{b}}", "{{a}} 7 {{b}}"),
    ],
)
def test_normalize_spaces_code_span_from_any_letter_or_digit(text, expected):
    assert normalize_jira_inline_spacing(text) == expected
    assert find_markup_contacts(normalize_jira_inline_spacing(text)) == []


@pytest.mark.parametrize(
    "text",
    [
        "{{a}}.",
        ".{{a}}",
        "{{a}}_b",
        "b_{{a}}",
        "{{a}}-b",
        "*{{a}}*",
        "_{{a}}_",
        "{{a}}{{b}}",
        "x {{a}} y",
        "{{a}}",
    ],
)
def test_normalize_leaves_code_span_with_other_neighbors_alone(text):
    assert normalize_jira_inline_spacing(text) == text
    assert find_markup_contacts(text) == []


def test_normalize_spaces_latin_next_to_code_span_nested_in_bold():
    text = "*b{{x}}c* and _d{{y}}7_"

    assert normalize_jira_inline_spacing(text) == "*b {{x}} c* and _d {{y}} 7_"


@pytest.mark.parametrize(
    "text",
    ["foo_bar_baz", "a*b*c", "snake_case_name", "2*3*4", "a*b + c*d"],
)
def test_normalize_leaves_identifiers_alone(text):
    assert normalize_jira_inline_spacing(text) == text


def test_normalize_keeps_bold_and_emphasis_hangul_only():
    text = "값*강조*값 b*bold*c x_emph_y"

    assert normalize_jira_inline_spacing(text) == "값 *강조* 값 b*bold*c x_emph_y"


def test_find_contacts_flags_latin_and_digit_neighbors():
    text = "ok {{a}} fine\nb{{x}}\n{{y}}7 and {{z}}.\n"

    contacts = find_markup_contacts(text)

    assert [(c.line, c.column, c.marker) for c in contacts] == [
        (2, 2, "{{"),
        (3, 4, "}}"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "{code}print(a{{x}}b){code}",
        "{noformat}a{{b}}c{noformat}",
        "{code:java}a{{b}}c{code}",
        "{code:html}<b>{{name}}s</b>\n<i>{{n}}</i>\n{code}",
        "{noformat}\n{{x}}y\n{noformat}",
        # Inside a block, only the matching bare closer ends it.
        "{code}a{{x}}b {noformat} c{{y}}d{code}",
        "{code}a{{x}}b{code:sql}c{{y}}d{code}",
    ],
)
def test_macro_content_is_left_alone_wherever_the_macro_sits_in_the_line(text):
    assert normalize_jira_inline_spacing(text) == text
    assert find_markup_contacts(text) == []


def test_prose_around_a_mid_line_macro_is_still_fixed():
    text = "앞{{a}}값 {code}z{code} 뒤{{b}}값"

    assert normalize_jira_inline_spacing(text) == (
        "앞 {{a}} 값 {code}z{code} 뒤 {{b}} 값"
    )
    assert [(c.line, c.column, c.marker) for c in find_markup_contacts(text)] == [
        (1, 2, "{{"),
        (1, 5, "}}"),
        (1, 24, "{{"),
        (1, 27, "}}"),
    ]


def test_a_macro_that_opens_mid_line_and_closes_on_a_later_line():
    text = "앞{{a}}값 {code}\nx{{b}}y\n{code} 뒤{{c}}값\n끝{{d}}값\n"

    assert normalize_jira_inline_spacing(text) == (
        "앞 {{a}} 값 {code}\nx{{b}}y\n{code} 뒤 {{c}} 값\n끝 {{d}} 값\n"
    )
    assert [(c.line, c.marker) for c in find_markup_contacts(text)] == [
        (1, "{{"),
        (1, "}}"),
        (3, "{{"),
        (3, "}}"),
        (4, "{{"),
        (4, "}}"),
    ]


def test_a_code_tag_with_a_brace_next_to_it_is_monospace_not_a_macro():
    prose = "앞{{code}}값 뒤{{noformat}}값"
    inside = "{code}a {{code}} b{{x}}c{code}"

    assert normalize_jira_inline_spacing(prose) == "앞 {{code}} 값 뒤 {{noformat}} 값"
    assert normalize_jira_inline_spacing(inside) == inside
    assert find_markup_contacts(inside) == []


@pytest.mark.parametrize(
    "text",
    [
        "my_table의 {{col_이름}} 컬럼",
        "2*3 {{x*값}} 끝",
        "2*3 {{a*b}} 끝",
        "snake_case 와 {{a_b_c}} 의 값",
        "*bold* 와 {{a*b}} 와 *bold*",
    ],
)
def test_marker_characters_inside_code_never_pair_with_markers_outside(text):
    assert normalize_jira_inline_spacing(text) == text


def test_a_false_emphasis_span_no_longer_hides_a_code_span_that_needs_spacing():
    assert normalize_jira_inline_spacing("a_b {{c_d}}s") == "a_b {{c_d}} s"
    assert normalize_jira_inline_spacing("my_table의 {{col_이름}}의") == (
        "my_table의 {{col_이름}} 의"
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("값*강조*값", "값 *강조* 값"),
        (".*강조*값", ".*강조* 값"),
        ("값_기울임_.", "값 _기울임_."),
        ("(_기울임_)값", "(_기울임_)값"),
        ("*값_강조_값*", "*값 _강조_ 값*"),
        # A rejected candidate must not swallow the marker that opens the next one.
        ("foo_bar 값_x_", "foo_bar 값 _x_"),
    ],
)
def test_marker_next_to_hangul_or_punctuation_is_spaced_from_hangul(text, expected):
    assert normalize_jira_inline_spacing(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "a*b*값",       # the opener follows a letter: not markup in Jira
        "값*b*c",       # the closer precedes a letter: not markup in Jira
        "한국*강조*b",
        "foo_bar_baz",
        "a*b*c",
        "my_table",
        "x_1_y 값",
    ],
)
def test_a_marker_pair_jira_cannot_read_as_markup_is_left_alone(text):
    assert normalize_jira_inline_spacing(text) == text


@pytest.mark.parametrize("text", ["{{x}}²", "{{x}}①", "{{x}}Ⅻ", "{{x}}½", "²{{x}}", "①{{x}}"])
def test_a_numeral_that_is_not_a_letter_or_digit_needs_no_space(text):
    """Jira breaks on letters and Nd digits only; No and Nl numerals render fine."""
    assert normalize_jira_inline_spacing(text) == text
    assert find_markup_contacts(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("{{x}}٣", "{{x}} ٣"),    # Nd: Arabic-Indic digit
        ("{{x}}１", "{{x}} １"),  # Nd: fullwidth digit
        ("{{x}}ª", "{{x}} ª"),    # Lo
        ("{{x}}ｰ", "{{x}} ｰ"),    # Lm
        ("ǅ{{x}}", "ǅ {{x}}"),    # Lt
    ],
)
def test_a_letter_or_decimal_digit_of_any_kind_needs_a_space(text, expected):
    assert find_markup_contacts(text) != []
    assert normalize_jira_inline_spacing(text) == expected
    assert find_markup_contacts(expected) == []


@pytest.mark.parametrize(
    "text",
    ["{{x}}}값", "{{x}}}s", "{{x}}}7", "a{{{x}} b", "값{{{x}} b", "a {{x}}}b {{y}}"],
)
def test_a_brace_run_next_to_code_is_a_literal_brace_not_a_contact(text):
    """Jira closes a span at the first ``}}`` and opens it at the last ``{{``."""
    assert find_markup_contacts(text) == []
    assert normalize_jira_inline_spacing(text) == text


def test_a_real_contact_is_still_flagged_next_to_a_brace_run():
    text = "{{x}}}값 and {{y}}값 and a{{{z}} and b{{w}}"

    assert [(c.column, c.marker) for c in find_markup_contacts(text)] == [
        (16, "}}"),
        (37, "{{"),
    ]
    assert normalize_jira_inline_spacing(text) == (
        "{{x}}}값 and {{y}} 값 and a{{{z}} and b {{w}}"
    )


# A {code}/{noformat} tag is a block only when a later bare tag of the same name
# closes it. Jira pairs tags in order over the whole body, reads \{code} as
# text, and renders a tag with no closing tag as an empty panel before it goes
# on reading prose. Each body below ends in prose that Jira renders literally.
UNCLOSED_OR_ESCAPED = [
    "Wrap it in \\{code} please.\n{{x}}값",
    "Use the {noformat} macro for logs.\n\n{{x}}값 here",
    "The macros {code} and {noformat} are fine.\n{{x}}값",
    "a{code}b {{x}}값",
    "x {code:java} y {{z}}값",
    "{code}a{code}b{code} {{x}}값",
    "text \\{noformat} {{x}}값",
    "{code}\n{{x}}값",
    "{code}a\\{code}b{code} {{x}}값",
    "\\\\\\{code}x{code} {{y}}값",
    "{code}a{noformat}b {{x}}값",
    "{noformat:title=T}a{{b}}c {{y}}값",
]


@pytest.mark.parametrize("text", UNCLOSED_OR_ESCAPED)
def test_prose_after_an_escaped_or_unclosed_tag_is_still_checked_and_fixed(text):
    contacts = find_markup_contacts(text)

    assert contacts, "the detector must see the literal braces after the tag"
    fixed = normalize_jira_inline_spacing(text)
    assert fixed != text
    assert find_markup_contacts(fixed) == []
    # Only whitespace was added.
    assert fixed.replace(" ", "") == text.replace(" ", "")


@pytest.mark.parametrize(
    "text",
    [
        "{code}a\\{code}b{code} {{x}} 값",
        "\\\\{code}x{code} {{y}} 값",
        "{code}a{noformat}b{code} {{x}} 값",
        "{code}a{code:java}b{code} {{x}} 값",
        "{noformat:title=T}\na{{b}}c\n{noformat}\n{{y}} 값",
        "{code:title=a|language=sql}\nx{{y}}z\n{code}",
        "{code}\nx{{y}}z\n{code}\n{code}\nu{{v}}w\n{code}",
    ],
)
def test_tags_pair_in_order_and_only_a_bare_same_name_tag_closes(text):
    """A paired block hides its content; an escaped tag, another macro's tag and a parameterized tag stay content."""
    assert normalize_jira_inline_spacing(text) == text
    assert find_markup_contacts(text) == []


def test_an_odd_run_of_backslashes_escapes_a_tag_and_an_even_run_does_not():
    # Two backslashes are a line break, so the tag is live and pairs with the
    # closer: the span between them is code. Three escape the first tag, which
    # leaves the closer to open a block that never closes: the span is prose.
    even = "\\\\{code}{{y}}z{code}"
    odd = "\\\\\\{code}{{y}}z{code}"

    assert normalize_jira_inline_spacing(even) == even
    assert find_markup_contacts(even) == []
    assert normalize_jira_inline_spacing(odd) == "\\\\\\{code}{{y}} z{code}"
    assert find_markup_contacts(odd) != []


def test_an_unclosed_tag_is_zero_width_and_its_line_stays_prose_on_both_sides():
    text = "앞{{a}}값 {code} 뒤{{b}}값\n{{c}}값"

    assert normalize_jira_inline_spacing(text) == (
        "앞 {{a}} 값 {code} 뒤 {{b}} 값\n{{c}} 값"
    )
    assert [(c.line, c.marker) for c in find_markup_contacts(text)] == [
        (1, "{{"),
        (1, "}}"),
        (1, "{{"),
        (1, "}}"),
        (2, "}}"),
    ]
