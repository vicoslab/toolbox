import contextlib

from fastapi import FastAPI, Request
from starlette.routing import Mount, Route

from .http import router
from .mcp import mcp


# The documentation shows that when mounting an MCP app, its session manager
# must be run within the parent application's lifespan.
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


def collect(router):
    routes = {}
    for r in router.routes:
        if isinstance(r, Route):
            routes[r.name] = set(r.param_convertors.keys())
        elif isinstance(r, Mount):
            routes.update(collect(r.app))
        else:
            raise TypeError(f"Unhandled route in collect: {r}")
    return routes


routes = collect(router)


def url_for_query(request: Request, route: str, **overrides):
    path, query = {}, {}
    path_params = routes.get(route, {})

    for propagating, params in [
        (True, request.query_params),
        (True, request.path_params),
        (False, overrides),
    ]:
        for k, v in params.items():
            if propagating and k not in ["model", "project", "experiment", "manifest"]:
                continue
            if k in path_params:
                path[k] = v
            else:
                query[k] = v
    return request.url_for(route, **path).include_query_params(**query)


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/"))


@app.middleware("http")
async def inject(request: Request, call_next):
    request.state.url_for_query = lambda route, **overrides: url_for_query(
        request, route, **overrides
    )
    return await call_next(request)
