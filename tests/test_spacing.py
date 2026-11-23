from cubrid_jira.spacing import normalize_korean_jira_spacing


def test_normalize_jira_code_span_next_to_korean_suffix():
    text = "주 회귀: {{xlocator_fetch_all}}의 값과 전체 {{X'..'}}로 출력"

    assert normalize_korean_jira_spacing(text) == (
        "주 회귀: {{xlocator_fetch_all}} 의 값과 전체 {{X'..'}} 로 출력"
    )


def test_normalize_jira_inline_markup_next_to_korean_prefix_and_suffix():
    text = "한국{{code}}값과 한국*강조*값과 한국_기울임_값"

    assert normalize_korean_jira_spacing(text) == (
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

    assert normalize_korean_jira_spacing(text) == (
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

    assert normalize_korean_jira_spacing(text) == (
        "본문 {{code}} 값\n```sql\nSELECT '본문{{code}}값';\n```\n"
    )


def test_normalize_jira_code_span_nested_in_bold():
    text = "* *{{REC_HOME}}이 아닌 행.* 병합은 *한국{{x}}값*끝"

    assert normalize_korean_jira_spacing(text) == (
        "* *{{REC_HOME}} 이 아닌 행.* 병합은 *한국 {{x}} 값* 끝"
    )


def test_normalize_markdown_code_span_nested_in_bold():
    from cubrid_jira.markdown import normalize_korean_markdown_spacing

    text = "- **`REC_HOME`이 아닌 행.** 끝 *한국`xy`값*"

    assert normalize_korean_markdown_spacing(text) == (
        "- **`REC_HOME` 이 아닌 행.** 끝 *한국 `xy` 값*"
    )


def test_find_contacts_reports_line_column_and_kind():
    from cubrid_jira.spacing import find_korean_markup_contacts

    text = "정상 {{ok}} 값\n* *{{REC_HOME}}이 아닌 행.*\n앞{{x}} 끝\n"

    contacts = find_korean_markup_contacts(text)

    assert [(c.line, c.column, c.marker) for c in contacts] == [
        (2, 14, "}}"),
        (3, 2, "{{"),
    ]
    assert contacts[0].text == "* *{{REC_HOME}}이 아닌 행.*"


def test_find_contacts_skips_code_blocks_and_korean_inside_monospace():
    from cubrid_jira.spacing import find_korean_markup_contacts

    text = (
        "{{한글}} 은 정상\n"
        "{code:sql}\n"
        "SELECT '{{x}}값';\n"
        "{code}\n"
        "```\n"
        "{{x}}값\n"
        "```\n"
    )

    assert find_korean_markup_contacts(text) == []


def test_normalized_output_has_no_contacts():
    from cubrid_jira.spacing import find_korean_markup_contacts

    text = "* *{{REC_HOME}}이 아닌 행.* 한국{{code}}값 _기울임{{y}}값_끝"

    assert find_korean_markup_contacts(normalize_korean_jira_spacing(text)) == []
