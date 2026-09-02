"""可恢复工作流目录与任务载荷。"""
from __future__ import annotations

from core.chapter_generation_service import PIPELINE_STAGES


WORKFLOWS = {
    "deep_chapter": {"label": "单章深度生成", "description": "上下文→提要→场景细纲→正文→修订→摘要→一致性", "steps": [*PIPELINE_STAGES, "commit", "memory", "consistency"], "model_calls": "3至5次，严格串行"},
    "serial_chapters": {"label": "连续章节生成", "description": "逐章执行完整流水线，质量异常自动暂停", "steps": ["chapter_loop", "quality_gate", "section_review"], "model_calls": "按章节串行"},
    "quality_sweep": {"label": "全书质量基线", "description": "不调用模型，检查交接覆盖、规划贴合与状态卡", "steps": ["memory_coverage", "planning_alignment", "state_coverage", "report"], "model_calls": "0次"},
}


def should_pause_for_commit(mode: str, quality_status: str, approved: bool = False) -> bool:
    if approved:
        return False
    if mode == "review":
        return True
    if mode == "automatic":
        return quality_status == "FAIL"
    return quality_status != "PASS"


def list_workflows() -> list[dict]:
    return [{"key": key, **value} for key, value in WORKFLOWS.items()]


def workflow_payload(key: str, values: dict) -> dict:
    if key not in WORKFLOWS:
        raise ValueError("未知工作流")
    payload = {"workflow": key, "workflow_steps": WORKFLOWS[key]["steps"], "workflow_completed": []}
    if key == "deep_chapter":
        payload.update({"count": 1, "target_words": max(500, min(20000, int(values.get("target_words", 5000)))), "stop_on_warning": bool(values.get("stop_on_warning", True)), "scene_mode": bool(values.get("scene_mode", False))})
    elif key == "serial_chapters":
        payload.update({"count": max(1, min(10, int(values.get("count", 3)))), "target_words": max(500, min(20000, int(values.get("target_words", 5000)))), "stop_on_warning": bool(values.get("stop_on_warning", True)), "scene_mode": bool(values.get("scene_mode", False))})
    return payload
