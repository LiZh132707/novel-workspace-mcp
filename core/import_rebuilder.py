"""把导入的纯正文分批反向重建为可继续创作的小说工程。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import config
from filelock import FileLock
from core.ai_contracts import BASE_SYSTEM, parse_object
from core.character_manager import CharacterManager
from core.chapter_commit_manager import ChapterCommitManager
from core.chapter_post_commit import ChapterPostCommitProcessor
from core.summary_manager import SummaryManager
from core.state_card_manager import StateCardManager
from core.prompt_settings import PromptSettingsManager
from core.mutation_transaction import NovelMutationTransaction
from storage_utils import StorageManager


class ImportRebuilder:
    def __init__(self, novel_manager, logger, llm=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.logger = logger
        self.llm = llm
        self.storage = storage or StorageManager(logger)
        self.batch_dir = self.root / "planning" / "import_batches"

    def _instruction(self) -> str:
        value = PromptSettingsManager(config.STORAGE_ROOT).instruction("import_rebuild")
        return f"\n用户可编辑规则：{value}" if value else ""

    def rebuild(self, progress=None, batch_size: int = 4) -> dict:
        files = sorted(
            (path for path in (self.root / "chapters").glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        if not files:
            raise ValueError("没有可重建的章节")
        analyses = []
        batches = [files[index:index + batch_size] for index in range(0, len(files), batch_size)]
        for index, batch in enumerate(batches):
            cache = self.batch_dir / f"{index + 1:04d}.json"
            fingerprint = self._batch_fingerprint(batch)
            cached = self.storage.safe_read_json(cache, {})
            data = cached.get("data") if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint else None
            if not isinstance(data, dict):
                data = self._analyze_batch(batch)
                self.storage.atomic_write_json(cache, {"fingerprint": fingerprint, "data": data})
            analyses.append(data)
            if progress:
                progress(f"已分析第{int(batch[0].stem)}—{int(batch[-1].stem)}章", 5 + int((index + 1) / len(batches) * 70), "import_analysis")
        synthesis = self._synthesize(analyses, len(files))
        with FileLock(str(self.root / ".novel_mutation.lock"), timeout=600), NovelMutationTransaction(
            self.root, [],
            directories=("bible", "outline", "planning", "characters", "summaries", "tracking", "timeline"),
            files=("state.json", "facts.json", "foreshadowing.json"),
        ):
            self._persist(analyses, synthesis, files)
        if progress:
            progress("人物、总纲、状态与章节记忆重建完成", 95, "import_commit")
        return {"chapters": len(files), "batches": len(batches), "characters": len(synthesis.get("characters", [])), "volumes": len(synthesis.get("volumes", []))}

    @staticmethod
    def _batch_fingerprint(files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _analyze_batch(self, files: list[Path]) -> dict:
        chapters = [{"chapter": int(path.stem), "text": path.read_text("utf-8", errors="replace")[:12000]} for path in files]
        if not self.llm:
            return {"chapters": [{"chapter": item["chapter"], "summary": item["text"][:300], "characters": [], "events": [], "state_changes": [], "open_loops": []} for item in chapters]}
        system = BASE_SYSTEM + """
你负责从已有小说正文中恢复工程数据。只输出JSON，不续写、不评价文风，不把推测当成事实。
人物、事件和状态必须附带正文证据；同名人物不要重复创建。""" + self._instruction()
        schema = {"chapters": [{"chapter": 1, "summary": "起因、行动、结果", "characters": [{"name": "姓名", "role": "作用", "evidence": "原文短句"}], "events": ["已发生事实"], "state_changes": [{"kind": "character/location/item/faction/relationship", "name": "对象", "field": "字段", "value": "章末值", "evidence": "原文短句"}], "open_loops": ["未闭环问题"]}]}
        raw = self.llm.chat(system, f"待分析章节：\n{json.dumps(chapters, ensure_ascii=False)}\n\n返回结构：{json.dumps(schema, ensure_ascii=False)}", max_tokens=2600, task_type="structured")
        data = parse_object(raw)
        return data if isinstance(data.get("chapters"), list) else {"chapters": []}

    def _synthesize(self, analyses: list[dict], total: int) -> dict:
        compact = []
        for batch in analyses:
            compact.extend(batch.get("chapters", []))
        if not self.llm:
            return {"world": "从导入正文逐步恢复，未确认内容请人工补充。", "outline": "\n".join(f"第{item.get('chapter')}章：{item.get('summary', '')}" for item in compact), "characters": [], "volumes": [{"title": "导入正文", "start_chapter": 1, "end_chapter": total, "goal": "承接已有正文继续创作", "sections": []}]}
        system = BASE_SYSTEM + """
你是小说拆书主编。依据逐章观测结果恢复全书结构，只输出JSON。
不得发明正文中不存在的重大设定；不确定内容放入uncertainties，不得写成权威事实。""" + self._instruction()
        schema = {"world": "正文确认的时代、地点、规则和势力", "outline": "已有主线及阶段变化", "characters": [{"name": "姓名", "role": "功能", "personality": "可确认特征", "background": "明确背景"}], "volumes": [{"title": "卷名", "start_chapter": 1, "end_chapter": total, "goal": "阶段结果", "sections": [{"title": "剧情弧", "start_chapter": 1, "end_chapter": total, "purpose": "作用", "outcome": "结果"}]}], "uncertainties": ["待确认事项"]}
        raw = self.llm.chat(system, f"逐章观测：\n{json.dumps(compact, ensure_ascii=False)[:70000]}\n\n总章数：{total}\n返回：{json.dumps(schema, ensure_ascii=False)}", max_tokens=4200, task_type="planning")
        return parse_object(raw)

    def _persist(self, analyses: list[dict], synthesis: dict, files: list[Path]):
        world = str(synthesis.get("world", "")).strip() or "从导入正文恢复的世界设定"
        outline = str(synthesis.get("outline", "")).strip() or "从导入正文恢复的主线"
        self.storage.atomic_write_text(self.root / "bible" / "world.md", "# 导入正文恢复的世界观\n\n" + world)
        self.storage.atomic_write_text(self.root / "outline" / "main.md", "# 导入正文恢复的总纲\n\n" + outline)
        volumes = synthesis.get("volumes", []) if isinstance(synthesis.get("volumes"), list) else []
        self.storage.atomic_write_json(self.root / "outline" / "volumes.json", volumes)
        self.storage.atomic_write_json(self.root / "planning" / "import_rebuild.json", {"completed": True, "uncertainties": synthesis.get("uncertainties", []), "chapters": len(files)})
        characters = CharacterManager(self.root, self.logger)
        for item in synthesis.get("characters", []) if isinstance(synthesis.get("characters"), list) else []:
            name = str(item.get("name", "")).strip()[:12]
            if name and not characters.get_character(name):
                role = {"核心配角": "重要配角", "次要配角": "次要角色"}.get(
                    str(item.get("role", "重要配角")), str(item.get("role", "重要配角")),
                )
                characters.create_character(
                    name, str(item.get("personality", "")), str(item.get("background", "")),
                    role_tier=role if role in {"主角", "重要配角", "次要角色", "NPC", "路人"} else "重要配角",
                )
        summaries = SummaryManager(self.nm, self.logger, None)
        commits = ChapterCommitManager(self.root, self.logger, self.storage)
        post_commit = ChapterPostCommitProcessor(self.nm, self.logger, self.storage)
        cards = StateCardManager(self.root, self.logger, self.storage)
        by_chapter = {int(item.get("chapter", 0)): item for batch in analyses for item in batch.get("chapters", []) if isinstance(item, dict)}
        for path in files:
            chapter = int(path.stem)
            content = path.read_text("utf-8", errors="replace")
            observation = by_chapter.get(chapter, {})
            data = summaries._basic_summary(chapter, content)
            data["summary"] = str(observation.get("summary", data["summary"]))[:1000]
            data["new_information"] = [str(item)[:500] for item in observation.get("events", []) if str(item).strip()][:20]
            data["handoff"]["open_loops"] = [str(item)[:500] for item in observation.get("open_loops", []) if str(item).strip()][:20]
            summaries.save_custom_summary(chapter, data)
            commits.mark(chapter, content, data)
            post_commit.run(chapter, content, {"summary": data})
            for change in observation.get("state_changes", []):
                if not isinstance(change, dict) or change.get("kind") not in cards.TYPES or not change.get("name") or not change.get("field"):
                    continue
                evidence = str(change.get("evidence", ""))
                if evidence and evidence in content:
                    cards.upsert(change["kind"], str(change["name"]), chapter, {str(change["field"]): change.get("value", "")}, evidence, "import_rebuild")
        latest_chapter = max((int(path.stem) for path in files), default=0)
        self.nm.save_state({
            "current_chapter": latest_chapter,
            "target_chapters": max(latest_chapter, int(self.nm.get_state().get("target_chapters", latest_chapter))),
        })
