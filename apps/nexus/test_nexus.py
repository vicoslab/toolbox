import subprocess
from pyquery import PyQuery
from pathlib import Path
import psutil
import os
import urllib.request
from urllib.parse import urljoin, urlsplit, parse_qs
import tempfile
import shutil
import json
import requests
import time
from sseclient import SSEClient

class NexusTestSession(requests.Session):
    def __init__(self, base_url=None, verify=True, headers={}):
        super().__init__()
        self.verify = verify
        self.base_url = base_url
        self.headers.update(headers)

    def request(self, method, url, *args, **kwargs):
        joined_url = urljoin(self.base_url, url)
        return super().request(method, joined_url, *args, **kwargs)

CACHE = Path(os.environ['TOOLBOX_CACHE'])

client = NexusTestSession(base_url='https://localhost', verify=False)
ls = NexusTestSession(base_url='http://localhost:8080', headers={ 'Authorization': f'Token {os.environ["LABEL_STUDIO_API_KEY"]}'})
testdata = Path('/data/cell-tiles-demo')
if not testdata.exists():
    with tempfile.NamedTemporaryFile() as f:
        urllib.request.urlretrieve('https://data.vicos.si/slaif/cell-tile-demo.zip', f.name)
        # archive contains folder cell-tiles-demo
        shutil.unpack_archive(f.name, testdata.parent, format='zip')

def test_read_main():
    assert (response := client.get('/')).status_code == 200
    pq = PyQuery(response.text)
    assert (tag := pq('title')) and 'Homepage' in tag.text()
    assert (tag := pq('h2')) and 'Welcome to' in tag.text()
    assert (tags := pq('ul.tour .tour-step')) and len(tags) > 0

def test_manage_groups():
    group = { 'owner':'TestManage','group':'TestGroup','models':['super-simple-net'],'url':'https://github.com/vicoslab/toolbox-models' }
    
    assert (response := client.post('/models/add', json=[group])).status_code == 200
    
    group_dir = CACHE / '.models' / 'TestManage' / 'TestGroup'
    assert (group_dir / 'super-simple-net').exists()
    assert (response := client.get('/models')).status_code == 200
    pq = PyQuery(response.text)
    assert (tag := pq('.model')) and tag.text().startswith('SuperSimpleNet')

    pinned = {'owner': group['owner'], 'group': group['group'], 'rev': '2a5f3f10667fb78ef23222440182970082e56f2c'}
    assert (response := client.post('/models/update', json=pinned)).status_code == 200
    assert response.json() == { 'rev': pinned['rev'] }
    assert subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=group_dir, text=True, capture_output=True).stdout.strip() == pinned['rev']
    assert (response := client.post('/models/update', json={k:v for (k,v) in pinned.items() if k != 'rev'})).status_code == 200
    assert response.json()['rev'] != pinned['rev']

    assert (response := client.post('models/remove', json=group)).status_code == 200
    assert not group_dir.exists() and not group_dir.parent.exists()

def helper_training(model, config):
    assert (response := client.post(f'/model/{model}/train', json=config)).status_code == 200
    assert (pid := response.json().get("pid")) and psutil.pid_exists(pid)
    psutil.Process(pid).wait(60 * 2)

    messages = SSEClient(f'/task/logs/{pid}/stream', session=client)
    weights = None
    for msg in messages:
        if msg.event == 'quick-action':
            kind, value = json.loads(msg.data)
            if kind == 'Weights':
                weights = value
        elif msg.event == 'eof':
            break
    assert weights is not None
    return weights

def helper_inference_ssn(alias, config, files, scores):
    assert (response := client.post('/model/super-simple-net/infer', json={ **config, 'alias': alias })).status_code == 200
    assert (pid := response.json().get("pid")) and psutil.pid_exists(pid)
    assert (response := client.get('/active')).status_code == 200
    assert alias in response.json()

    time.sleep(5) # gateway scans active models every 5s
    assert (response := requests.post(f'http://localhost:9090/infer/{alias}', files=[('images', open(f, 'rb')) for f in files])).status_code == 200
    assert all(a > b for a,b in zip(response.json()['scores'], scores))

    assert (response := client.post(f'/task/stop/{pid}')).status_code == 200
    psutil.Process(pid).wait(60)

def helper_install_model(group):
    model = group['models'][0]
    if (CACHE / model).exists():
        return
    assert (response := client.post('/models/add', json=[group])).status_code == 200
    assert (response := client.post(f'/model/{model}/install')).status_code == 200
    response = response.json()
    assert (pid := response.get('pid')) and 'logs' in response
    assert (response := client.get('/task/status')).status_code == 200
    pq = PyQuery(response.text)
    assert (tags := pq('.task')) and any((model in tag.text for tag in tags))
    assert psutil.pid_exists(pid)
    psutil.Process(pid).wait(60 * 5)

def test_model_ssn():
    helper_install_model({ 'owner':'TestInstall','group':'TestGroup','models':['super-simple-net'],'url':'https://github.com/vicoslab/toolbox-models' })
    weights = helper_training('super-simple-net', {'manifest': str(testdata / 'manifest.json'), 'epochs': 2, 'batch': 16})
    helper_inference_ssn('test', {'weights': weights }, [testdata / 'damaged_0_0000_ls3_camera0.jpg'], [0.9])

def test_import_export():
    helper_install_model({ 'owner':'TestInstall','group':'TestGroup','models':['super-simple-net'],'url':'https://github.com/vicoslab/toolbox-models' })
    assert (response := client.get('/datasets')).status_code == 200
    assert testdata.name in response.json()['dirs']

    body = { 'dataset': str(testdata), 'group_size': 1, 'group_separation': 'interlace', 'regex_include': r'\.jpg', 'regex_exclude': 'damaged'}
    assert (response := client.post('/dataset?model=super-simple-net', data=body)).status_code == 200
    assert (project := parse_qs(urlsplit(response.url).query).get('project', [None])[0])

    time.sleep(5) # give label studio some time to process upload
    assert (response := ls.get(f'/api/tasks?project={project}&page_size=200&include=id')).status_code == 200
    assert len(tasks := response.json()['tasks']) > 0
    for task in tasks[:20]:
        assert ls.post(f'/api/tasks/{task["id"]}/annotations', json={}).status_code == 201
    
    exportdir = Path('/data/testexport')
    export_request = {'project': project, 'task': 'anomaly-detection', 'dir': str(exportdir) }
    assert (response := client.post('/export', json=export_request)).status_code == 200
    assert (pid := response.json()['pid']) and psutil.pid_exists(pid)
    psutil.Process(pid).wait(60)
    assert (manifest := exportdir / 'manifest.json').exists()

    weights = helper_training('super-simple-net', {'manifest': str(manifest), 'epochs': 2, 'batch': 16})
    helper_inference_ssn('test', {'weights': weights }, [testdata / 'damaged_0_0000_ls3_camera0.jpg'], [0])
