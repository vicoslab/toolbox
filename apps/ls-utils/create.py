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

from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

DATASET_DIR = Path(os.environ['LOCAL_FILES_DOCUMENT_ROOT'])
API_KEY = os.environ['LABEL_STUDIO_USER_TOKEN']
TASK = os.environ['TASK']
DATASET = os.environ['DATASET']
PROJECT_TITLE = os.getenv('PROJECT_TITLE')

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

class Handler(BaseHTTPRequestHandler):
    def _json(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "UP"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/setup":
            self._json({
                "model_version": "stub",
                "extra_params": {}
            })
        else:
            length = int(self.headers.get('Content-Length', 0))
            if length:
                self.rfile.read(length)

            self._json({})

Thread(target=lambda: HTTPServer(("0.0.0.0", 9090), Handler).serve_forever(), daemon=True).start()

ls.ml.create(title="Inference worker", project=project.id, url="http://localhost:9090")
