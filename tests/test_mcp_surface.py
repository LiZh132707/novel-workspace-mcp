import asyncio
import json
import threading
import time

import novel_server
from core.novel_manager import NovelManager
from storage_utils import StorageManager


def test_new_management_tools_are_exposed_and_dispatched():
    tools = asyncio.run(novel_server.list_tools())
    names = {tool.name for tool in tools}
    expected = {
        "list_facts", "list_foreshadowing", "list_character_changes", "decide_character_change", "export_novel",
        "list_ai_actions", "list_workflows", "get_scene_outlines", "save_scene_outline", "get_state_cards",
        "list_genre_packs", "apply_genre_pack", "generate_story_sandbox", "evaluate_long_form",
        "get_planning_impacts", "review_chapter_memory",
        "list_state_proposals", "decide_state_proposal", "get_author_preferences",
        "get_review_queue", "list_canonical_locks", "upsert_canonical_lock", "remove_canonical_lock",
        "get_story_clock", "set_travel_rule", "remove_travel_rule",
        "list_prompt_snapshots", "compare_prompt_snapshot", "set_prompt_baseline",
        "evaluate_rag", "rebuild_imported_novel",
        "revise_history", "list_history_revisions", "commit_history_revision", "abort_history_revision",
        "get_causal_graph", "propose_causal_repairs", "apply_causal_repairs",
    }
    assert expected <= names
    assert expected <= novel_server.HANDLERS.keys()


def test_mcp_scene_rewrite_keeps_event_loop_responsive(monkeypatch):
    worker_threads = []

    class FakeClient:
        @staticmethod
        def chat(*_args, **_kwargs):
            worker_threads.append(threading.get_ident())
            time.sleep(0.05)
            return "重写结果"

    class FakeChapterManager:
        @staticmethod
        def read_chapter(_chapter):
            worker_threads.append(threading.get_ident())
            time.sleep(0.02)
            return "章节正文"

    monkeypatch.setattr(novel_server, "get_llm", lambda: FakeClient())
    monkeypatch.setattr(novel_server, "chm", lambda: FakeChapterManager())
    monkeypatch.setattr(
        novel_server, "scene_revision_prompts", lambda *_args: ("system", "prompt"),
    )
    monkeypatch.setattr(
        novel_server.workspace, "get_current_novel", lambda: {"name": "测试书"},
    )

    async def run():
        ticks = 0
        done = False

        async def ticker():
            nonlocal ticks
            while not done:
                ticks += 1
                await asyncio.sleep(0.005)

        task = asyncio.create_task(ticker())
        result = await novel_server.rewrite_scene(1, "场景")
        done = True
        await task
        return result, ticks

    result, ticks = asyncio.run(run())
    assert result == "重写结果"
    assert ticks >= 3
    assert all(thread_id != threading.get_ident() for thread_id in worker_threads)


def test_mcp_governance_tools_are_functional(tmp_path, monkeypatch):
    storage = StorageManager(novel_server.logger)
    novel = NovelManager("MCP治理书", tmp_path, novel_server.logger, storage)
    monkeypatch.setattr(novel_server, "nm", lambda: novel)

    async def run():
        await novel_server.upsert_canonical_lock("character", "林舟", "current_status", "存活", "主角保护")
        locks = json.loads(await novel_server.list_canonical_locks())
        await novel_server.set_travel_rule("旧城", "港口", 90)
        clock = json.loads(await novel_server.get_story_clock())
        queue = json.loads(await novel_server.get_review_queue())
        removed_rule = json.loads(await novel_server.remove_travel_rule("旧城", "港口"))
        removed_lock = json.loads(await novel_server.remove_canonical_lock(locks[0]["id"]))
        return locks, clock, queue, removed_rule, removed_lock

    locks, clock, queue, removed_rule, removed_lock = asyncio.run(run())
    assert locks[0]["value"] == "存活"
    assert clock["travel_rules"][0]["minutes"] == 90
    assert queue["blocking"] == 0
    assert removed_rule["removed"] is True
    assert removed_lock["removed"] is True


def test_mcp_call_keeps_original_novel_bound_across_switch_and_worker_thread(monkeypatch):
    class FakeNovel:
        def __init__(self, name):
            self.name = name

    current = {"manager": FakeNovel("小说甲"), "info": {"name": "小说甲"}}
    started = asyncio.Event()
    release = asyncio.Event()

    monkeypatch.setattr(
        novel_server.workspace, "capture_current",
        lambda: (current["manager"], current["info"]),
    )

    async def probe():
        started.set()
        await release.wait()
        threaded_name = await asyncio.to_thread(lambda: novel_server.nm().name)
        return f"{novel_server.nm().name}/{novel_server.current_novel_info()['name']}/{threaded_name}"

    monkeypatch.setitem(novel_server.HANDLERS, "binding_probe", probe)

    async def run():
        task = asyncio.create_task(novel_server.call_tool("binding_probe", {}))
        await started.wait()
        current.update({"manager": FakeNovel("小说乙"), "info": {"name": "小说乙"}})
        release.set()
        return await task

    response = asyncio.run(run())
    assert response[0].text == "小说甲/小说甲/小说甲"
