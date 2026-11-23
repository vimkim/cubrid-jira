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
