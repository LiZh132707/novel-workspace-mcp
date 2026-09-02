import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.causal_graph_manager import CausalGraphManager
from core.causal_repair_planner import CausalRepairPlanner
from core.planning_review_manager import PlanningReviewManager


def create_router(get_novel_manager, logger, storage_mgr) -> APIRouter:
    router = APIRouter()

    @router.get("/api/novels/{name}/causal-graph")
    async def api_causal_graph(name: str):
        try:
            novel_manager = get_novel_manager(name)
            graph = CausalGraphManager(
                novel_manager.path, logger, storage_mgr
            ).build(novel_manager.get_current_chapter())
            return JSONResponse({"success": True, "graph": graph})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @router.post("/api/novels/{name}/causal-repairs/propose")
    async def api_propose_causal_repairs(name: str, request: Request):
        try:
            payload = await request.json()
            novel_manager = get_novel_manager(name)
            proposal = CausalRepairPlanner(
                novel_manager.path, logger, storage_mgr
            ).propose(int(payload.get("window", 3) or 3))
            return JSONResponse({"success": True, "proposal": proposal})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @router.post("/api/novels/{name}/causal-repairs/{proposal_id}/apply")
    async def api_apply_causal_repairs(name: str, proposal_id: str):
        try:
            novel_manager = get_novel_manager(name)
            proposal = await asyncio.to_thread(
                CausalRepairPlanner(novel_manager.path, logger, storage_mgr).apply,
                proposal_id,
            )
            return JSONResponse({"success": True, "proposal": proposal})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @router.get("/api/novels/{name}/planning-reviews")
    async def api_planning_reviews(name: str):
        try:
            novel_manager = get_novel_manager(name)
            report = PlanningReviewManager(
                novel_manager.path, logger, storage_mgr
            ).report()
            return JSONResponse({"success": True, "report": report})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @router.post("/api/novels/{name}/planning-reviews/volume-tasks/{task_id}")
    async def api_decide_volume_repair_task(
        name: str, task_id: str, request: Request
    ):
        try:
            payload = await request.json()
            novel_manager = get_novel_manager(name)
            result = PlanningReviewManager(
                novel_manager.path, logger, storage_mgr
            ).decide_volume_task(
                task_id,
                str(payload.get("status", "resolved")),
                str(payload.get("note", "")),
                int(payload.get("evidence_chapter", 0) or 0),
                str(payload.get("evidence_quote", "")),
                bool(payload.get("waive", False)),
            )
            return JSONResponse({"success": True, **result})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    return router
