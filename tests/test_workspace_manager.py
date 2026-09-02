import logging
import tempfile
from pathlib import Path

import core.workspace_manager as workspace_module
from core.workspace_manager import WorkspaceManager


LOGGER = logging.getLogger("workspace-manager-test")


def _manager(root: Path, monkeypatch) -> WorkspaceManager:
    monkeypatch.setattr(workspace_module, "WORKSPACE_FILE", root / "workspace.json")
    monkeypatch.setattr(workspace_module, "NOVELS_ROOT", root / "novels")
    return WorkspaceManager(LOGGER)


def test_failed_creation_rollback_restores_previous_current_novel(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = _manager(root, monkeypatch)
        workspace.create_novel("原小说")
        workspace.create_novel("失败的新小说")
        workspace.rollback_created("失败的新小说", "原小说")
        assert workspace.data["current"] == "原小说"
        assert workspace._current_novel == "原小说"
        assert set(workspace.data["novels"]) == {"原小说"}


def test_registration_update_is_persisted_under_workspace_lock(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = _manager(root, monkeypatch)
        workspace.create_novel("导入书")
        workspace.update_registration("导入书", {"genre": "悬疑", "status": "待校对"})
        reopened = WorkspaceManager(LOGGER)
        assert reopened.data["novels"]["导入书"]["genre"] == "悬疑"
        assert reopened.data["novels"]["导入书"]["status"] == "待校对"
        assert reopened.data["current"] == "导入书"


def test_workspace_wrong_json_shape_degrades_to_empty_registry(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setattr(workspace_module, "WORKSPACE_FILE", root / "workspace.json")
        monkeypatch.setattr(workspace_module, "NOVELS_ROOT", root / "novels")
        (root / "workspace.json").write_text("[]", "utf-8")
        workspace = WorkspaceManager(LOGGER)
        assert workspace.data == {"novels": {}, "current": None}
        workspace.create_novel("恢复创建")
        assert workspace.data["current"] == "恢复创建"


def test_capture_current_returns_matching_manager_and_info(monkeypatch, tmp_path):
    workspace = _manager(tmp_path, monkeypatch)
    workspace.create_novel("绑定书")
    manager, info = workspace.capture_current()
    assert manager.name == info["name"] == "绑定书"
    assert manager.path == workspace_module.NOVELS_ROOT / "绑定书"
