import json
import modelargs
import os
from pathlib import Path
from openai_codex import Codex, CodexConfig
from openai_codex.types import Personality
import subprocess
import sys

CACHE = os.environ['TOOLBOX_CACHE']

flavor = sys.argv[1]
if flavor == 'codex':
    CODEX_HOME = Path(os.environ['TOOLBOX_DATA'], 'agents', 'codex')
    CODEX_HOME.mkdir(exist_ok=True, parents=True)
    old = None
    config = CodexConfig(
        env={
            'CODEX_HOME': str(CODEX_HOME),
        },
        config_overrides=[
            'default_permissions = "toolbox"',
            'permissions.toolbox.extends = ":workspace"',
            'mcp_servers.toolbox.url = "http://localhost:8079/mcp"',
            # Works but it seems like theres no useful tools exposed
            # 'mcp_servers.mlflow.command = "uv"',
            # 'mcp_servers.mlflow.args = ["run", "mlflow", "mcp", "run"]',
            # 'mcp_servers.mlflow.env = { MLFLOW_TRACKING_URI = "http://localhost:8081", MLFLOW_MCP_TOOLS = "experiments,runs" }',
            # 'mcp_servers.mlflow.cwd = "/opt/apps/mlflow"',
        ],
    )
    with Codex(config) as codex:
        try:
            if codex.account() is None:
                login = codex.login_chatgpt_device_code()

                print('Please log in with the following device code', login.user_code, flush=True)
                modelargs.emit_action('Info', json.dumps(dict(title="Login", description="Use provided code to add device to your account.", link=login.verification_url)))
                succ = login.wait().success
                modelargs.emit_action('Clear', '')
                if not succ:
                    print('Login failed')
                    exit(1)

            print('Login successful')
            thread = codex.thread_start(model="gpt-6-luna")
            while True:
                prompt = json.loads(input())
                print('>', prompt, flush=True)
                turn = thread.turn(prompt)

                saw_delta = False
                completed_status = None
                completed_texts = []

                for event in turn.stream():
                    if event.method == "item/agentMessage/delta":
                        if (delta := event.payload.delta):
                            print(delta, end="", flush=True)
                            saw_delta = True
                    elif event.method == "item/completed":
                        if (root := event.payload.item.root).type == "agentMessage":
                            completed_texts.append(root.text)
                    elif event.method == "turn/completed":
                        completed_status = event.payload.turn.status.value

                if completed_status is None:
                    raise RuntimeError("stream ended without turn/completed")

                print("" if saw_delta else "".join(completed_texts).strip())
        except EOFError:
            pass
