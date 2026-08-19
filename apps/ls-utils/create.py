import os
from label_studio_sdk import LabelStudio
from pathlib import Path
import json
import yaml
import re
import xml.etree.ElementTree as ET

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']
MODEL_DIR = Path(os.environ['MODEL_DIR'])

request = json.loads(os.environ['CREATION_REQUEST'])
config_file = MODEL_DIR / 'config.yml'
if not config_file.exists():
    raise ValueError(f'Model dir "{MODEL_DIR}" does not contain LS config.')
with open(config_file) as f:
    config = yaml.safe_load(f)['config']

ls = LabelStudio(base_url='http://localhost:8080', api_key=API_KEY)
size = request['group_size']

view = ET.fromstring(config)
for node in view:
    if node.tag == 'Image':
        if size > 1 and 'valueList' not in node.attrib:
            raise ValueError("Cannot use group size > 1 with LabelStudio config without valueList in Image.")

# todo: do any models support one/multiple images as input at the same time?
project = ls.projects.create(label_config=config, title=request['title'])

print(project.id)

p = re.compile(request['regex'])
if (dataset := request['dataset']) and (dataset := Path(dataset)).exists():
    # need to create import storage regardless otherwise some permissions check fails and you get 404s
    ls.import_storage.local.create(
        project=project.id,
        title=f'{request["title"]} dataset' if request['title'] else str(dataset),
        path=str(dataset),
        # recursive_scan=True,
        use_blob_urls=True,
        regex_filter=request['regex']
    )

    LABEL_STUDIO_HOST = os.environ['LABEL_STUDIO_HOST']
    files = sorted([f'{LABEL_STUDIO_HOST}/data/local-files/?d={x.relative_to(DATASET_DIR)}' for x in dataset.rglob("*") if x.is_file() and p.match(str(x))])
    if size == 1:
        tasks = [ { 'image': file } for file in files]
    elif request['group_separation'] == 'interlace':
        tasks = [ { 'images': files[i:i+size] } for i in range(0, len(files), size)]
    elif request['group_separation'] == 'divide':
        block_size = len(tasks) / size
        tasks = list(map(lambda x: { 'images': list(x) }, zip(*[files[i:i+block_size] for i in range(0, len(files), block_size)])))
    else:
        raise ValueError('Group separation has invalid value')
    ls.projects.import_tasks(id=project.id, request=[{"data": task} for task in tasks])
    (dataset / 'groups.json').write_text(json.dumps({ 'group_size': size, 'regex': request['regex'] }))

extra = dict(model=MODEL_DIR.name, project=project.id)
ls.ml.create(title="Inference worker", project=project.id, url="http://localhost:9090", is_interactive=True, extra_params=json.dumps(extra))
