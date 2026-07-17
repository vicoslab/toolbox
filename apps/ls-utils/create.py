import os
from label_studio_sdk import LabelStudio
from pathlib import Path
import io
import re
import json
import yaml

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']
MODEL_DIR = Path(os.environ['MODEL_DIR'])
DATASET = os.getenv('DATASET')
PROJECT_TITLE = os.getenv('PROJECT_TITLE')

config_file = MODEL_DIR / 'config.yml'
if not config_file.exists():
    raise ValueError(f'Model dir "{MODEL_DIR}" does not contain LS config.')
with open(config_file) as f:
    config = yaml.safe_load(f)['config']

ls = LabelStudio(base_url='http://localhost:8080', api_key=API_KEY)
project = ls.projects.create(label_config=config, **{ 'title': PROJECT_TITLE } if PROJECT_TITLE else {})

print(project.id)

if DATASET:
    # api doesn't support recursive scan yet for some reason
    import_storage = ls.import_storage.local.create(
        project=project.id,
        title=f'{PROJECT_TITLE} dataset' if PROJECT_TITLE else DATASET,
        path=str(DATASET_DIR / DATASET),
        # recursive_scan=True,
        use_blob_urls=True,
        regex_filter="[^.]*.(jpe?g|png)"
    )
    # TODO: test this once recursive_scan is available, and make api enddpoint set this process to nonblocking
    ls.import_storage.local.sync(import_storage.id)

extra = dict(model=MODEL_DIR.name, project=project.id)
ls.ml.create(title="Inference worker", project=project.id, url="http://localhost:9090", is_interactive=True, extra_params=json.dumps(extra))
