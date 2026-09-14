from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from replayforge.api.app import create_app
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings


def test_viewer_catalog_authorization_and_no_cache(tmp_path: Path) -> None:
    runtime = build_runtime(
        RuntimeSettings(  # type: ignore[call-arg]  # Pydantic runtime dotenv override.
            _env_file=None,
            evidence_directory=tmp_path,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    done = Event()
    try:
        controller = runtime.execution_controller
        assert controller is not None
        client = TestClient(create_app(runtime.api_services))
        response = client.get("/api/v1/executions/catalog")
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert len(response.json()["capabilities"]) == len(controller.registry.all())
        assert len({item["id"] for item in response.json()["capabilities"]}) == 3
        for capability in response.json()["capabilities"]:
            artifact = controller.registry.get(capability["id"], capability["version"]).artifact
            assert capability["description"] == artifact.capability.description
            assert capability["inputs"] == artifact.inputs.model_dump(mode="json")
            assert capability["outputs"] == artifact.outputs.model_dump(mode="json")
        assert len(response.json()["presets"]) == 3
        assert response.json()["discovery_ready"] is False

        def operation() -> dict[str, str]:
            done.wait(5)
            return {"status": "success"}

        started = controller.viewer.start("replay", operation)
        path = f"/api/v1/executions/{started['execution_id']}"
        assert client.get(path).status_code == 404
        headers = {"X-Viewer-Token": started["viewer_token"]}
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["state"] == "running"
        assert started["viewer_token"] not in response.text
        assert client.get(f"{path}/frames/999", headers=headers).status_code == 410
        assert client.get(f"{path}/frames/999").status_code == 404
        assert client.get(path + "?after=-1", headers=headers).status_code == 422
        response = client.post(
            "/api/v1/executions",
            json={
                "execution": {
                    "mode": "replay",
                    "capability_id": "member.temporary_card_lock",
                    "tenant": "harbor",
                    "inputs": {"unexpected": "private input must not be echoed"},
                }
            },
        )
        assert response.status_code == 422
        assert "private input" not in response.text
        response = client.post(
            "/api/v1/executions",
            json={
                "execution": {
                    "mode": "discovery",
                    "goal": "Find a synthetic account value",
                    "tenant": "harbor",
                    "application_family": "northstar_member_service",
                    "entry_point": "legacy_servicing",
                    "inputs": {},
                }
            },
        )
        assert response.status_code == 503
    finally:
        done.set()
        runtime.close()
