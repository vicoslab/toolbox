import os
from label_studio_sdk import LabelStudio
from label_studio_sdk.converter import brush
import time
from pathlib import Path
from datetime import datetime
import json
import io
import re
import numpy as np
from PIL import Image

DATASET_DIR = Path(os.getenv('LOCAL_FILES_DOCUMENT_ROOT'))
API_KEY = os.getenv('LABEL_STUDIO_USER_TOKEN')
TASK = os.getenv('TASK')
DATASET = os.getenv('DATASET')
PROJECT_TITLE = os.getenv('PROJECT_TITLE', f'{DATASET}: {TASK}')

ls = LabelStudio(base_url='http://localhost:8080', api_key=API_KEY)

with open(f'./templates/{TASK}.html') as f:
    project = ls.projects.create(
        label_config=f.read(),
        **{ 'title': PROJECT_TITLE } if PROJECT_TITLE else {},
    )

print(project.id)

# api doesn't support recursive scan yet for some reason
import_storage = ls.import_storage.local.create(
    project=project.id,
    title=f'{PROJECT_TITLE} dataset' if PROJECT_TITLE else DATASET,
    path=str(DATASET_DIR / DATASET),
    # recursive_scan=True,
    use_blob_urls=True,
    regex_filter=".*(jpe?g|png)"
)
# TODO: test this once recursive_scan is available, and make api enddpoint set this process to nonblocking
ls.import_storage.local.sync(import_storage.id)
