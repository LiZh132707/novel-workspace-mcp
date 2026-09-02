from core.planning_application import protect_committed_opening


def test_replanning_preserves_committed_opening_and_only_takes_future_entries():
    old = {"chapters": [
        {"chapter": 1, "title": "已发生一"},
        {"chapter": 2, "title": "已发生二"},
        {"chapter": 3, "title": "旧未来"},
    ], "rolling_plan": "旧规则"}
    new = {"chapters": [
        {"chapter": 1, "title": "试图改写过去"},
        {"chapter": 2, "title": "试图改写过去二"},
        {"chapter": 3, "title": "新未来"},
        {"chapter": 4, "title": "新未来二"},
    ], "rolling_plan": "新规则"}
    result = protect_committed_opening(old, new, 2)
    assert [item["title"] for item in result["chapters"]] == ["已发生一", "已发生二", "新未来", "新未来二"]
    assert result["protected_chapters"] == [1, 2]
    assert result["rolling_plan"] == "新规则"


def test_replanning_does_not_invent_missing_past_opening_metadata():
    result = protect_committed_opening({}, {"chapters": [
        {"chapter": 1, "title": "模型补写过去"},
        {"chapter": 6, "title": "未来"},
    ]}, 5)
    assert result["chapters"] == [{"chapter": 6, "title": "未来"}]
