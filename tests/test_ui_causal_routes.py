import logging

from fastapi import FastAPI

from storage_utils import StorageManager
from ui.routes.causal import create_router


def test_causal_route_contract_is_stable():
    app = FastAPI()
    app.include_router(
        create_router(
            lambda _name: None,
            logging.getLogger("causal-route-test"),
            StorageManager(logging.getLogger("causal-route-test")),
        )
    )

    operations = {
        (method.upper(), path, detail["operationId"])
        for path, methods in app.openapi()["paths"].items()
        for method, detail in methods.items()
    }
    assert operations == {
        (
            "GET",
            "/api/novels/{name}/causal-graph",
            "api_causal_graph_api_novels__name__causal_graph_get",
        ),
        (
            "POST",
            "/api/novels/{name}/causal-repairs/propose",
            "api_propose_causal_repairs_api_novels__name__causal_repairs_propose_post",
        ),
        (
            "POST",
            "/api/novels/{name}/causal-repairs/{proposal_id}/apply",
            "api_apply_causal_repairs_api_novels__name__causal_repairs__proposal_id__apply_post",
        ),
        (
            "GET",
            "/api/novels/{name}/planning-reviews",
            "api_planning_reviews_api_novels__name__planning_reviews_get",
        ),
        (
            "POST",
            "/api/novels/{name}/planning-reviews/volume-tasks/{task_id}",
            "api_decide_volume_repair_task_api_novels__name__planning_reviews_volume_tasks__task_id__post",
        ),
    }
