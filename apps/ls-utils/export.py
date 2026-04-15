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
PROJECT_ID = os.getenv('PROJECT_ID')

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

# Check if dataset is well formed for image export
p = re.compile(r'^https://.*/data/local-files/\?d=(.+)$')
def get_dataset(x):
    m = p.match(x['image'])
    if m:
        m = m.groups()[0].split('/', maxsplit=1)
    return { **x, 'image': m }

resolved = list(map(get_dataset, j))
if resolved[0]['image'] is None or any(map(lambda x: x is None or x['image'][0] != resolved[0]['image'][0], resolved)):
    with open(DATASET_DIR / f'export-{date}.json') as f:
        json.dump(j, f)
    print('Warning: only saving json because image dataset in export is not well formed (all files need to belong to a single subdirectory of a dataset in local storage).')
    exit(1)
j = resolved

data = []
EXPORT_DIR = DATASET_DIR / os.getenv('EXPORT_DIR', f"{resolved[0]['image'][0]}/.export-{date}")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

for task in j:
    _, relpath = task['image']
    item = { 'image_path': '../' + relpath }
    
    if 'labels' in task:
        labels = None
        mask = None
        for tag in task['labels']:
            if tag['format'] == 'rle':
                if labels is None:
                    width = tag['original_width']
                    height = tag['original_height']
                    labels = tag['labels']
                    mask = np.reshape(brush.decode_rle(tag['rle']), [height, width, 4])[:, :, 3]
                elif labels != tag['labels']:
                    print(f'Warning: mixing different labels in `{relpath}`')
                else:
                    mask += np.reshape(brush.decode_rle(tag['rle']), [height, width, 4])[:, :, 3]
        filename = (EXPORT_DIR / relpath).with_suffix('.label.png')
        filename.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(mask).save(filename)
        item['label'] = 'abnormal' if mask.sum() > 0 else 'normal'
        item['mask_path'] = str(filename.relative_to(EXPORT_DIR))
    
    data.append(item)

with open(EXPORT_DIR / 'manifest.json', 'w') as f:
    json.dump({ 'data': data }, f)

print(f"Project {PROJECT_ID} successfully exported to '{EXPORT_DIR}'")
