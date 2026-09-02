import logging
import tempfile
from pathlib import Path

from core.prompt_settings import PROMPT_CATALOG, PromptSettingsManager


def test_prompt_settings_are_categorized_and_persistent():
    with tempfile.TemporaryDirectory() as tmp:
        manager = PromptSettingsManager(Path(tmp), logging.getLogger("test"))
        prompts = manager.get()
        assert {"基础规则", "创建与策划", "文风", "章节创作", "章后整理", "修改与质量"} <= set(prompts)
        prompts["章节创作"]["chapter_write"]["instruction"] = "优先使用具体动作。"
        manager.save(prompts)
        assert manager.instruction("chapter_write") == "优先使用具体动作。"
        assert manager.reset() == PROMPT_CATALOG
