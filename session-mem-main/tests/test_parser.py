from session_mem.llm.parser import safe_json_loads


def test_safe_json_loads_direct():
    assert safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_code_block():
    text = '```json\n{"a": 1}\n```'
    assert safe_json_loads(text) == {"a": 1}


def test_safe_json_loads_code_block_invalid_fallback():
    """代码块内容不是合法 JSON，应 fallback 到后续策略。"""
    text = "```json\n{not json}\n```"
    assert safe_json_loads(text) is None


def test_safe_json_loads_braces_wrapped():
    text = '  {"a": 1}  '
    assert safe_json_loads(text) == {"a": 1}


def test_safe_json_loads_braces_wrapped_invalid():
    """首尾花括号但内容非法，应 fallback。"""
    text = "{not json}"
    assert safe_json_loads(text) is None


def test_safe_json_loads_brackets_wrapped():
    text = "  [1, 2, 3]  "
    assert safe_json_loads(text) == [1, 2, 3]


def test_safe_json_loads_brackets_wrapped_invalid():
    """首尾方括号但内容非法，应 fallback。"""
    text = "[not json]"
    assert safe_json_loads(text) is None


def test_safe_json_loads_regex_braces():
    text = 'some text {"a": 1} more text'
    assert safe_json_loads(text) == {"a": 1}


def test_safe_json_loads_regex_braces_trailing_comma():
    text = 'text {"a": 1,} more'
    assert safe_json_loads(text) == {"a": 1}


def test_safe_json_loads_regex_braces_invalid():
    text = "text {not json} more"
    assert safe_json_loads(text) is None


def test_safe_json_loads_regex_brackets():
    text = "text [1, 2, 3] more"
    assert safe_json_loads(text) == [1, 2, 3]


def test_safe_json_loads_regex_brackets_trailing_comma():
    text = "text [1, 2, 3,] more"
    assert safe_json_loads(text) == [1, 2, 3]


def test_safe_json_loads_regex_brackets_invalid():
    text = "text [not json] more"
    assert safe_json_loads(text) is None


def test_safe_json_loads_empty_string():
    assert safe_json_loads("") is None


def test_safe_json_loads_no_json_structure():
    assert safe_json_loads("hello world") is None
