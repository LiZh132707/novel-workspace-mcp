import tempfile
import logging
from pathlib import Path

from core.project_schema import PROJECT_SCHEMA_VERSION, PROJECT_TYPE, ProjectSchemaManager
from storage_utils import StorageManager


def test_new_project_schema_is_explicit_and_self_validating():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = ProjectSchemaManager(root, StorageManager(logging.getLogger("project-schema-test")))
        assert manager.validate()["valid"] is False
        manifest = manager.initialize("新小说")
        assert manifest["schema_version"] == PROJECT_SCHEMA_VERSION
        assert manifest["project_type"] == PROJECT_TYPE
        assert manager.validate()["valid"] is True
