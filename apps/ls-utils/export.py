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

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']
PROJECT_ID = os.environ['PROJECT_ID']
TASK = os.environ['TASK']

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
        export_type='JSON_MIN',
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
for task in j:
    if m := p_dataset.match(task['image']):
        root, relpath = m.groups()[0].split('/', maxsplit=1)
        if dataset is None:
            dataset = root
        elif dataset != root:
            valid = False
        resolved.append({ **task, 'image': ('dataset', relpath) })
    elif m := p_upload.match(task['image']):
        id, filename = m.groups()
        if id != PROJECT_ID:
            valid = False
        resolved.append({ **task, 'image': ('upload', filename) })
    else:
        valid = False

if not valid:
    print('Warning: only saving json because image dataset in export failed validation.')
    with open(DATASET_DIR / f'export-{date}.json', 'w') as f:
        json.dump(j, f)
    exit(1)
j = resolved

data = []
UPLOAD_DIR = Path(os.environ['LABEL_STUDIO_BASE_DATA_DIR']) / 'media' / 'upload' / PROJECT_ID
EXPORT_DIR = DATASET_DIR / os.getenv('EXPORT_DIR', f'{dataset or f"ls-project-{PROJECT_ID}"}/.export-{date}')
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_UPLOADS = EXPORT_DIR.parent / f".export-uploads-{PROJECT_ID}"

for task in j:
    source, relpath = task['image']
    if source == 'dataset':
        item = { 'image_path': '../' + relpath }
    elif source == 'upload':
        EXPORT_UPLOADS.mkdir(parents=True, exist_ok=True)
        shutil.copy(UPLOAD_DIR / relpath, EXPORT_UPLOADS / relpath)
        item = { 'image_path': f'../.export-uploads-{PROJECT_ID}/' + relpath }
    else:
        raise ValueError("Invalid source for task image")
    
    labels = None
    mask = None
    points = None
    if 'labels' in task:
        for tag in task['labels']:
            if rle := tag.get('rle'):
                if labels is None:
                    width = tag['original_width']
                    height = tag['original_height']
                    labels = tag['labels']
                    mask = np.reshape(brush.decode_rle(rle), [height, width, 4])[:, :, 3]
                elif labels != tag['labels']:
                    print(f'Warning (skipping): mixing different labels in `{relpath}`')
                else:
                    mask += np.reshape(brush.decode_rle(rle), [height, width, 4])[:, :, 3]
            elif verts := tag.get('vertices'):
                if len(verts) != 2:
                    print(f'Warning (skipping): vertices len != 2')
                    continue

                if points is None:
                    points = []
                    size = {
                        'x': tag['original_width'],
                        'y': tag['original_height'],   
                    }
                
                points.append([int(v[p]/100*size[p]) for v in verts for p in ['x', 'y']])

    if TASK == 'anomaly-detection':
        if mask is not None:
            filename = (EXPORT_DIR / relpath).with_suffix('.label.png')
            filename.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(mask).save(filename)
            item['label'] = 'abnormal' if mask.sum() > 0 else 'normal'
            item['mask_path'] = str(filename.relative_to(EXPORT_DIR))
        elif task['annotator'] is not None:
            item['label'] = 'normal'
    elif TASK == 'orientation-estimation':
        if points is not None:
            item['points'] = points
    
    data.append(item)

manifest = str(EXPORT_DIR / 'manifest.json')
with open(manifest, 'w') as f:
    json.dump({ 'data': data }, f)

print(f"Project {PROJECT_ID} successfully exported to '{EXPORT_DIR}'")
print(f"Manifest:", manifest)
