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

tools = request.get('tools') or {}
tools_config = []

if smtoolsline := next((line for line in config.split('\n') if '@SMART_TOOLS@' in line), None):
    lspace = smtoolsline[:smtoolsline.index('@SMART_TOOLS@')]

    i = 0
    print('tools', tools, flush=True)
    if items := tools.get('rectangle'):
        tools_config.append('<Header size="5" value="Bounding box based models that return segmentations of instances similar to selected region." />')
        tools_config.append('<RectangleLabels name="toolbox-tools-bbox" toName="image" smartOnly="true">')
        for item in items:
            tools_config.append(f'\t<Label value="{item}" hotkey=" "/>')
            i += 1
        tools_config.append('</RectangleLabels>')
    if items := tools.get('keypoint'):
        tools_config.append('<Header size="5" value="Keypoint based segmentation assistance. Hold down alt (or option) for negative examples." />')
        tools_config.append('<KeypointLabels name="toolbox-tools-keypoint" toName="image" smartOnly="true" dynamic="true">')
        for item in items:
            tools_config.append(f'\t<Label value="{item}" hotkey=" "/>')
            i += 1
        tools_config.append('</KeypointLabels>')

    config = config.replace('@SMART_TOOLS@', ('\n' + lspace).join(tools_config))

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

p = re.compile(request['regex_include'])
p_not = re.compile(request['regex_exclude'] or "(?!.*)")
print('Using patterns', p, p_not)
if (dataset := request['dataset']) and (dataset := Path(dataset)).exists():
    # need to create import storage regardless otherwise some permissions check fails and you get 404s
    ls.import_storage.local.create(
        project=project.id,
        title=f'{request["title"]} dataset' if request['title'] else str(dataset),
        path=str(dataset),
        # recursive_scan=True,
        use_blob_urls=True,
    )

    LABEL_STUDIO_HOST = os.environ['LABEL_STUDIO_HOST']
    files = sorted([f'{LABEL_STUDIO_HOST}/data/local-files/?d={x.relative_to(DATASET_DIR)}' for x in dataset.rglob("*") if x.is_file() and p.search(str(x)) and not p_not.search(str(x))])
    if size == 1:
        tasks = [ { 'image': file } for file in files]
    elif request['group_separation'] == 'interlace':
        tasks = [ { 'images': files[i:i+size] } for i in range(0, len(files), size)]
    elif request['group_separation'] == 'divide':
        block_size = len(tasks) / size
        tasks = list(map(lambda x: { 'images': list(x) }, zip(*[files[i:i+block_size] for i in range(0, len(files), block_size)])))
    else:
        raise ValueError('Group separation has invalid value')
    if tasks:
        ls.projects.import_tasks(id=project.id, request=[{"data": task} for task in tasks])
    (dataset / 'groups.json').write_text(json.dumps({ 'group_size': size, 'regex_include': request['regex_include'], 'regex_exclude': request['regex_exclude'] }))

extra = dict(model=MODEL_DIR.name, project=project.id)
ls.ml.create(title="Inference worker", project=project.id, url="http://localhost:9090", is_interactive=True, extra_params=json.dumps(extra))
