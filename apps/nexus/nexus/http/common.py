import os
from enum import Enum

from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .. import config


class TaskResponse(BaseModel):
    pid: int
    logs: str
    duplicate: bool = False


class TourStep(Enum):
    START = 0
    MODEL_SELECTION = 1
    DATASET = 2
    LABELING = 3
    EXPORT = 4
    TRAINING = 5
    MONITORING = 6
    INFERENCE = 7
    FINISH = 8


TOUR_STEPS = [
    # id, title, endpoint, train_only
    (TourStep.START.value, "Start", "index", False),
    (TourStep.MODEL_SELECTION.value, "Models", "models", False),
    (TourStep.DATASET.value, "Dataset", "dataset_get", True),
    (TourStep.LABELING.value, "Labeling", "label", True),
    (TourStep.EXPORT.value, "Export", "export_get", True),
    (TourStep.TRAINING.value, "Training", "train", True),
    (TourStep.MONITORING.value, "Experiments", "dashboard", True),
    (TourStep.INFERENCE.value, "Inference", "infer", False),
    (TourStep.FINISH.value, "Finish tour", "finish", False),
]


templates = Jinja2Templates(directory="templates")
templates.env.globals.update(
    {
        "tour_steps": TOUR_STEPS,
        "tour_enum": TourStep,
        "brand_name_long": os.environ["TOOLBOX_BRAND_NAME_LONG"],
        "brand_name_short": os.environ["TOOLBOX_BRAND_NAME_SHORT"],
        "model_manifest": config.model_manifest,
    }
)  # ty: ignore[no-matching-overload]
