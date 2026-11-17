"""One lazily started local backend shared by all stdio MCP clients.

Loopback only, per-run random authentication, no cloud calls, no OS autostart.
Exit after 30 minutes with no MCP requests; the next request reconciles again.
"""

from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json, sys, subprocess, secrets, time, threading, hmac, os
import requests
from orimem import journal_state
from .backend import Backend, BASE
from .mcp_server import call
from .watcher import Watcher
from .content_policy import local_content as sanitize

ENDPOINT = BASE / "service-state.json"


def read_state():
    try:
        data = json.loads(ENDPOINT.read_text(encoding="utf-8"))
        if (
            type(data.get("port")) is not int
            or not 1 <= data["port"] <= 65535
            or not isinstance(data.get("key"), str)
        ):
            raise ValueError("Invalid local service metadata")
        return data
    except (OSError, ValueError):
        return None


def rpc_once(state, name, args):
    # Session trust_env=False prevents private queries from using a proxy.
    session = requests.Session()
    session.trust_env = False
    with session:
        r = session.post(
            f'http://127.0.0.1:{state["port"]}/rpc',
            headers={"X-OriMem-Key": state["key"]},
            json={"name": name, "arguments": args},
            timeout=(2, 240),
        )
        r.raise_for_status()
        value = r.json()
    if value.get("service") != "orion-local-v1":
        raise ValueError("Unexpected local service")
    if "error" in value:
        raise ValueError(value["error"])
    return value["result"]


def ensure():
    state = read_state()
    if state:
        try:
            rpc_once(state, "health", {})
            return state
        except (requests.RequestException, ValueError):
            pass
    with journal_state.locked(BASE / "service-start.json"):
        state = read_state()
        if state:
            try:
                rpc_once(state, "health", {})
                return state
            except (requests.RequestException, ValueError):
                pass
        # Shared by every agent: detach it from the agent that happened to start it.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        child = None
        for extra in ((getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),) if os.name == "nt" else ()) + (0,):
            try:
                child = subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-m", "orimem", "service"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags | extra,
                    start_new_session=(os.name != "nt"),
                )
                break
            except OSError:
                continue
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = read_state()
            if state:
                try:
                    rpc_once(state, "health", {})
                    return state
                except (requests.RequestException, ValueError):
                    pass
            if child.poll() is not None:
                raise RuntimeError("Local OriMem backend exited during startup")
            time.sleep(0.25)
        raise TimeoutError("Local OriMem backend startup timed out")


def remote_call(name, args):
    return rpc_once(ensure(), name, args)


def stop_service():
    state = read_state()
    if not state:
        return {"stopped": False, "reason": "not-running"}
    try:
        result = rpc_once(state, "_stop", {})
    except requests.RequestException:
        return {"stopped": False, "reason": "already-unreachable"}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = read_state()
        if not current or current.get("pid") != state.get("pid"):
            return result
        time.sleep(0.25)
    return dict(result, pending_exit=True)


def run_service():
    from orimem import config as _config
    _config.ensure_home()
    try:
        from orimem.upgrade import run_if_needed
        run_if_needed()
    except Exception:
        pass               # an upgrade step must never keep the memory service from starting
    backend = Backend()
    auth = secrets.token_urlsafe(32)
    last_request = [time.monotonic()]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/rpc" or not hmac.compare_digest(
                self.headers.get("X-OriMem-Key", ""), auth
            ):
                self.send_error(403)
                return
            try:
                count = int(self.headers.get("Content-Length", "0"))
                if not 1 <= count <= 2_000_000:
                    raise ValueError("Request size is outside budget")
                args = json.loads(self.rfile.read(count))
                last_request[0] = time.monotonic()
                if args["name"] == "_stop":
                    result = {"stopped": True, "pid": os.getpid()}
                    threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    result = call(backend, args["name"], args.get("arguments", {}))
                data = {"service": "orion-local-v1", "result": sanitize(result)}
            except Exception as error:
                message = (
                    type(error).__name__
                    if type(error).__module__.startswith("jsonschema")
                    else sanitize(str(error))
                )
                data = {"service": "orion-local-v1", "error": message}
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state = dict(port=server.server_address[1], key=auth, pid=os.getpid(), service="orion-local-v1")
    from orimem import config as _config
    _config.write_private(ENDPOINT, json.dumps(state).encode("utf-8"))
    watcher = Watcher(backend, collect=True).start()

    def idle_monitor():
        while True:
            time.sleep(10)
            if time.monotonic() - last_request[0] > 1800:
                server.shutdown()
                return

    threading.Thread(target=idle_monitor, daemon=True).start()
    try:
        server.serve_forever(poll_interval=1)
    finally:
        watcher.close()
        server.server_close()
        current = read_state()
        if current and current.get("pid") == os.getpid():
            ENDPOINT.unlink(missing_ok=True)
