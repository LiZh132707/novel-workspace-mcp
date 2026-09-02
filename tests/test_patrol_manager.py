from core.patrol_manager import PatrolManager


def test_patrol_schedule_is_tiered_without_model_calls():
    assert PatrolManager.due_for_chapter(1) == {"health": False, "long_form": False}
    assert PatrolManager.due_for_chapter(10) == {"health": True, "long_form": False}
    assert PatrolManager.due_for_chapter(20) == {"health": True, "long_form": False}
    assert PatrolManager.due_for_chapter(25) == {"health": False, "long_form": True}
    assert PatrolManager.due_for_chapter(50) == {"health": True, "long_form": True}
