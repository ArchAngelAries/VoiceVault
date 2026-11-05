"""Dataset review and portable export endpoints for OmniVoice training data."""
from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from training.datasets import DatasetError, DatasetService, capabilities

router = APIRouter()


class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    speaker: str | None = Field(default=None, max_length=100)
    language_id: str | None = Field(default=None, max_length=32)
    reference_ids: list[str] = Field(min_length=1, max_length=500)


class PatchClipRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    reviewed: bool | None = None
    enabled: bool | None = None


def _error(exc: DatasetError) -> HTTPException:
    status = 404 if "not found" in str(exc).lower() or "missing" in str(exc).lower() else 400
    return HTTPException(status, str(exc))


@router.get("/capabilities")
async def get_capabilities():
    def report():
        result = capabilities()
        if result["training_dependencies_ready"]:
            try:
                jobs = _jobs()
                validated = any(jobs.has_validated_model(job["model_id"])
                                for job in jobs.list_jobs() if job.get("status") == "completed")
            except ValueError as exc:
                result["training_history_problem"] = str(exc)
                result["reason"] = str(exc)
                return result
            result["training_available"] = validated
            result["training_runtime"]["training_validated"] = validated
            if validated:
                result["reason"] = "A local model passed a one-step compatibility check. Voice quality still needs listening review."
        return result
    return await asyncio.to_thread(report)


@router.get("/references")
async def list_references():
    return {"items": await asyncio.to_thread(DatasetService().references_list)}


@router.get("/datasets")
async def list_datasets():
    return {"items": await asyncio.to_thread(DatasetService().list_datasets)}


@router.post("/datasets")
async def create_dataset(body: CreateDatasetRequest):
    try:
        return await asyncio.to_thread(DatasetService().create_dataset, body.name, body.speaker,
                                       body.language_id, body.reference_ids)
    except DatasetError as exc:
        raise _error(exc) from exc


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str):
    try:
        return await asyncio.to_thread(DatasetService().get_dataset, dataset_id)
    except DatasetError as exc:
        raise _error(exc) from exc


@router.get("/datasets/{dataset_id}/clips/{clip_id}/audio")
async def get_clip_audio(dataset_id: str, clip_id: str):
    try:
        path: Path = await asyncio.to_thread(DatasetService().clip_audio_path, dataset_id, clip_id)
    except DatasetError as exc:
        raise _error(exc) from exc
    return FileResponse(path, media_type="audio/wav", filename=f"{clip_id}.wav")


@router.patch("/datasets/{dataset_id}/clips/{clip_id}")
async def patch_clip(dataset_id: str, clip_id: str, body: PatchClipRequest):
    if body.text is None and body.reviewed is None and body.enabled is None:
        raise HTTPException(400, "Provide text, reviewed, or enabled to update.")
    try:
        service = DatasetService()
        return await asyncio.to_thread(service.patch_clip, dataset_id, clip_id, text=body.text,
                                        reviewed=body.reviewed, enabled=body.enabled)
    except DatasetError as exc:
        raise _error(exc) from exc


@router.post("/datasets/{dataset_id}/export")
async def export_dataset(dataset_id: str):
    try:
        return await asyncio.to_thread(DatasetService().export, dataset_id)
    except DatasetError as exc:
        raise _error(exc) from exc


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    try:
        path: Path = await asyncio.to_thread(DatasetService().artifact_path, artifact_id)
    except DatasetError as exc:
        raise _error(exc) from exc
    return FileResponse(path, media_type="application/zip", filename=f"omnivoice-dataset-{artifact_id}.zip")


class StartTrainingRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    model_id: str = Field(min_length=1, max_length=500)
    steps: int = Field(default=1, ge=1, le=10000)
    batch_tokens: int = Field(default=256, ge=256, le=2048)
    save_steps: int = Field(default=1, ge=1, le=1000)


_job_service = None
_job_service_lock = Lock()


def _jobs():
    # Construct only when used; importing the API must not create files or workers.
    global _job_service
    with _job_service_lock:
        if _job_service is None:
            from training.jobs import TrainJobService
            _job_service = TrainJobService()
    return _job_service


async def shutdown_training_jobs():
    # Avoid starting a service just to stop it during ordinary application exit.
    with _job_service_lock:
        service = _job_service
    if service is not None:
        await asyncio.to_thread(service.shutdown)


def _model_registry():
    from api.tts import _get_services
    return _get_services()[0]


def _training_models():
    from training.publication import PublicationService
    publisher = PublicationService(_jobs().data_dir)
    def name(model):
        folder = Path(model.path)
        if folder.parent.resolve() == publisher.root.resolve():
            publication = publisher.get(folder.name)
            if publication:
                return f"{publication['dataset_name']} (trained)"
        if folder.parent.name == "snapshots" and folder.parent.parent.name.startswith("models--"):
            return folder.parent.parent.name.removeprefix("models--").replace("--", "/")
        return model.family if folder.name.lower() == "model" else f"{model.family} · {folder.name}"
    return {"items": [{"id": model.id, "name": name(model),
                       "size_gb": round(model.size_bytes / 1e9, 2),
                       "state": model.state.value, "reason": model.reason,
                       "training_validated": _jobs().has_validated_model(model.id, model)}
                      for model in _model_registry().models() if model.engine_id == "omnivoice"]}


@router.get("/models")
async def training_models():
    return await asyncio.to_thread(_training_models)


@router.get("/jobs")
async def list_training_jobs():
    def report():
        from training.publication import PublicationService
        service = _jobs()
        publisher = PublicationService(service.data_dir)
        items = service.list_jobs()
        for job in items:
            if job.get("status") == "completed":
                job["publication"] = publisher.get(job["id"])
        return {"items": items}
    return await asyncio.to_thread(report)


@router.post("/jobs")
async def start_training(body: StartTrainingRequest):
    def submit():
        model = _model_registry().get(body.model_id)
        if model is None or model.engine_id != "omnivoice":
            raise ValueError("Choose an installed OmniVoice model to train.")
        return _jobs().submit(body.dataset_id, model, steps=body.steps,
                              batch_tokens=body.batch_tokens, save_steps=body.save_steps)
    try:
        return await asyncio.to_thread(submit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs/{job_id}")
async def get_training_job(job_id: str):
    try:
        return await asyncio.to_thread(_jobs().get_job, job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
async def cancel_training_job(job_id: str):
    try:
        return await asyncio.to_thread(_jobs().cancel, job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/jobs/{job_id}/publish")
async def publish_training_job(job_id: str):
    def publish():
        from core.model_paths import model_roots
        from core import settings
        from training.publication import PublicationService
        service = _jobs()
        publication = PublicationService(service.data_dir).publish(service.get_job(job_id))
        # Publishing is explicit and preserves the source. Discovery reads metadata;
        # it must never promote the new voice to inference-tested on this basis.
        model_id, warning = None, None
        try:
            registry = _model_registry()
            registry.set_roots(model_roots(settings.load()))
            models = registry.discover()
            model_id = next((model.id for model in models
                             if Path(model.path).resolve() == Path(publication["path"]).resolve()), None)
            if model_id is None:
                warning = "The checkpoint was added, but model discovery did not recognize it. Rescan models in Generate."
        except Exception:
            warning = "The checkpoint was added, but the model list could not refresh. Rescan models in Generate."
        return {"publication": publication, "model_id": model_id,
                "registry_refreshed": model_id is not None, "warning": warning}
    try:
        return await asyncio.to_thread(publish)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/jobs/{job_id}/log")
async def training_log(job_id: str):
    try:
        return {"text": await asyncio.to_thread(_jobs().log_tail, job_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

