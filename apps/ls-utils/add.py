import os
from label_studio_sdk import LabelStudio
from pathlib import Path
import json
import yaml

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
LABEL_STUDIO_HOST = os.environ['LABEL_STUDIO_HOST']
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']


request = json.loads(os.environ['ADDITION_REQUEST'])
project = request['project']
upload = Path(request['upload_dir'])
if (upload.parent / 'groups.json').exists():
    with open(upload.parent / 'groups.json') as f:
        j = json.load(f)
        size = j["group_size"]
else:
    size = 1

files = sorted([f'{LABEL_STUDIO_HOST}/data/local-files/?d={x.relative_to(DATASET_DIR)}' for x in upload.rglob("*") if x.is_file()])
if size == 1:
    tasks = [ { 'image': file } for file in files]
elif request['group_separation'] == 'interlace':
    tasks = [ { 'images': files[i:i+size] } for i in range(0, len(files), size)]
elif request['group_separation'] == 'divide':
    block_size = len(tasks) / size
    tasks = list(map(lambda x: { 'images': list(x) }, zip(*[files[i:i+block_size] for i in range(0, len(files), block_size)])))
else:
    raise ValueError('Group separation has invalid value')

ls = LabelStudio(base_url='http://localhost:8080', api_key=API_KEY)
ls.projects.import_tasks(id=project, request=[{"data": task} for task in tasks])
print('Imported', len(tasks), 'tasks')
