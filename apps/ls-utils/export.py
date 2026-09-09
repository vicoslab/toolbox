import os
from label_studio_sdk import LabelStudio
from label_studio_sdk.converter import brush
import time
from pathlib import Path
from datetime import datetime
import json
import io
import re
import shutil
import numpy as np
from PIL import Image
import importlib
import site

site.addsitedir(os.environ['MODEL_FILES'])
try:
    import ls_adapter as model
except ImportError as err:
    print('Could not load model files')
    exit(1)

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']
PROJECT_ID = os.environ['PROJECT_ID']
COMBINE = os.getenv('COMBINE')

date = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

# Setup export
ls = LabelStudio(base_url='http://localhost:8080', api_key=API_KEY)
job = ls.projects.exports.create(id = PROJECT_ID, title=f'export-{PROJECT_ID}-{date}')

# Poll until completed or failed
start = time.time()
print('Waiting for export snapshot to complete')
while job.status not in ('completed', 'failed'):
    print('.', end='')
    if time.time() - start > 300:
        raise TimeoutError(f'Export job timed out')
    time.sleep(1.0)

if job.status == 'failed':
    print('Export failed: {job}')
    exit(1)
print('\nGot data from label studio')

# Parse into json
with io.BytesIO() as b:
    for chunk in ls.projects.exports.download(
        id=PROJECT_ID,
        export_pk=job.id,
        export_type='JSON',
        request_options={'chunk_size': 1024},
    ):
        b.write(chunk)
    j = json.loads(b.getvalue())

valid = True
# Check if dataset is well formed for image export
p_dataset = re.compile(r'^https://.*/data/local-files/\?d=(.+)$')
p_upload = re.compile(r'^/app/label-studio/data/upload/(\d+)/(.+)$')

dataset = None
resolved = []

def detect_storage(image):
    global dataset
    global valid
    if m := p_dataset.match(image):
        root, relpath = m.groups()[0].split('/', maxsplit=1)
        if dataset is None:
            dataset = root
        elif dataset != root:
            valid = False
        return ('dataset', relpath)
    elif m := p_upload.match(image):
        id, filename = m.groups()
        if id != PROJECT_ID:
            valid = False
        return ('upload', filename)
    else:
        valid = False

for task in j:
    if image := task['data'].get('image'):
        task['data']['image'] = detect_storage(image)
    elif images := task['data'].get('images'):
        task['data']['images'] = [detect_storage(im) for im in images]
    resolved.append(task)

if not valid:
    print('Warning: only saving json because image dataset in export failed validation.')
    with open(DATASET_DIR / f'export-{date}.json', 'w') as f:
        json.dump(j, f)
    exit(1)
j = resolved

# determine and validate export dir
dataset = DATASET_DIR / (dataset or f'ls-project-{PROJECT_ID}')
if EXPORT_DIR := os.getenv('EXPORT_DIR'):
    EXPORT_DIR = Path(EXPORT_DIR)
    EXPORT_UPLOADS = EXPORT_DIR / f".export-uploads-{PROJECT_ID}"
else:
    EXPORT_DIR = dataset / f'.export-{date}'
    EXPORT_UPLOADS = EXPORT_DIR.parent / f".export-uploads-{PROJECT_ID}"
if not EXPORT_DIR.is_relative_to(DATASET_DIR):
    raise ValueError('Export dir points outside data root')
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

data = []
UPLOAD_DIR = Path(os.environ['LABEL_STUDIO_BASE_DATA_DIR']) / 'media' / 'upload' / PROJECT_ID

def get_path(source, relpath):
    if source == 'dataset':
        return str((dataset / relpath).relative_to(EXPORT_DIR, walk_up=True))
    elif source == 'upload':
        EXPORT_UPLOADS.mkdir(parents=True, exist_ok=True)
        shutil.copy(UPLOAD_DIR / relpath, EXPORT_UPLOADS / relpath)
        return str((EXPORT_UPLOADS / relpath).relative_to(EXPORT_DIR, walk_up=True))
    else:
        raise ValueError("Invalid source for task image")

split_mapping = {
    'Train': 'train',
    'Validation': 'val',
    'Test': 'test',
}
splits = {}
for task in j:
    item = {}
    results = [x['result'] for x in task['annotations']]
    if image := task['data'].get('image'):
        source, relpath = image
        item['image_path'] = get_path(source, relpath)
        if len(task['annotations']) > 0:
            item.update(model.export(results, EXPORT_DIR, [relpath], False) or {})
    elif images := task['data'].get('images'):
        item['images'] = [get_path(*im) for im in images]
        if len(task['annotations']) > 0:
            item.update(model.export(results, EXPORT_DIR, [relpath for (_, relpath) in images], False) or {})
    
    split = split_mapping.get(task.get('split'), 'data')
    if split not in splits:
        splits[split] = []
    splits[split].append(item)

manifest = str(EXPORT_DIR / 'manifest.json')
with open(manifest, 'w') as f:
    if COMBINE and (combine := Path(COMBINE)).exists():
        old = json.loads(combine.read_text())
        for split, items in splits.items():
            old[split] = old.get(split, []) + items
        old['version'] = 4
        json.dump(old, f)
    else:
        splits['version'] = 4
        json.dump(splits, f)

print(f"Project {PROJECT_ID} successfully exported to '{EXPORT_DIR}'")
print(f"Toolbox:Manifest:", manifest)
