from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError

from . import config, logic, tasks
from .logic import ToolboxError

mcp = MCPServer("Toolbox")
mcp.resource("toolbox://smart-tools")(logic.tools_list)
mcp.resource("toolbox://workers")(logic.active_models)

mcp.tool("models.add")(logic.models_add)
mcp.tool("models.update")(logic.models_update)
mcp.tool("models.remove")(logic.models_remove)


@mcp.resource("toolbox://models")
def models():
    """Mapping of currently loaded model definitions."""
    return {
        k: {**v, "installed": (config.CACHE / k).exists()}
        for k, v in config.model_manifest.items()
    }


@mcp.tool("models.train")
def train(options: dict, model: str):
    """Start training run with given options."""
    return {
        "pid": logic.train(ToolboxError.mcp, options, model),
        "message": "Started training run.",
    }


@mcp.tool("models.infer")
def infer(options: dict, model: str, alias: str | None = None):
    """Start inference worker with given options. Alias must be unique among running workers (model id is used by default)."""
    return {
        "pid": logic.infer(ToolboxError.mcp, options, model, alias),
        "message": "Started inference worker.",
    }


@mcp.tool("models.install")
def install(model: str):
    """Start model installation for given model id."""
    return {
        "pid": logic.install(ToolboxError.mcp, model),
        "message": "Started installation.",
    }


@mcp.tool("models.uninstall")
def uninstall(model: str):
    """Remove installation directory of given model id."""
    return logic.uninstall(ToolboxError.mcp, model)


@mcp.tool("tasks.stop")
def stop_task(pid: int):
    """Stop execution of specified task."""
    if not (task := tasks.tasks.get(pid)):
        raise ToolError("Task does not exist")
    if not (proc := task["process"]):
        raise ToolError("Task already exited")
    proc.terminate()


@mcp.resource("toolbox://logs/{pid}")
def logs_mcp(pid: int):
    """Get logs for specified task. If task is still running, logs may be incomplete and running=True will be returned in the response."""
    if not (task := tasks.tasks.get(pid)):
        raise ResourceNotFoundError()
    return {
        "logs": task["output"],
        "running": task["process"] is not None,
    }
