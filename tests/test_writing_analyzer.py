import logging

from core.writing_analyzer import WritingAnalyzer, detect_clusters, detect_repeated_openings


def test_chinese_chapter_uses_character_units_not_single_whitespace_word():
    analyzer = WritingAnalyzer(logging.getLogger("test"))
    text = "林舟沿着长街前进，观察两侧店铺和行人的反应。" * 30
    result = analyzer.detect_patterns(text)
    assert result["word_count"] > 300
    assert result["severity_score"] < 100


def test_chinese_cluster_window_uses_character_positions():
    text = "轻轻地" + "甲" * 220 + "轻轻地" + "乙" * 220 + "轻轻地"
    matches = [{"pattern": "adverb", "char_pos": index, "line": 1, "context": ""} for index in (
        text.find("轻轻地"), text.find("轻轻地", 10), text.rfind("轻轻地"),
    )]
    assert detect_clusters(text, matches) == []


def test_chinese_repeated_sentence_openings_are_detected():
    text = "他推开门走进大厅。他推开门看向楼梯。他推开门听见脚步。"
    issues = detect_repeated_openings(text)
    assert issues and issues[0]["count"] == 3


def test_short_sentences_count_in_pacing():
    result = WritingAnalyzer(logging.getLogger("test")).analyze_pacing("好。走！Yes.")
    assert result["sentences"] == 3
    assert result["avg_sentence_length"] == 1.7


def test_identical_paragraphs_keep_distinct_line_locations():
    paragraph = "他推开门走进大厅。他推开门看向楼梯。他推开门听见脚步。"
    issues = detect_repeated_openings("  " + paragraph + "\n\n  " + paragraph)
    assert [item["line"] for item in issues] == [1, 3]
