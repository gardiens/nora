import os
import sys
import json
import time
import psutil
import subprocess
import socket
import atexit
import platform
import re
import requests
import shutil
from pathlib import Path
from typing import Union, List, Dict

__all__ = ['translate_from_url', 'translate_from_identifier']

IS_WINDOWS = os.name == "nt"

SERVER_PORT = 1969
SERVER_IP = f"http://127.0.0.1:{SERVER_PORT}"
PING_URL = f"{SERVER_IP}/connector/ping"
ARXIV_ABS_URL_RE = re.compile(r"^(https?://arxiv\.org/abs/\d{4}\.\d{4,5})([A-Za-z])$")
ARXIV_PDF_URL_RE = re.compile(r"^https?://arxiv\.org/pdf/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?$", re.IGNORECASE)
CVF_PDF_URL_RE = re.compile(
    r"^(https?://openaccess\.thecvf\.com/(?:content(?:_[^/]+)?|content/[^/]+))/papers/(.+)\.pdf$",
    re.IGNORECASE)

# Global server process (singleton pattern)
_translation_process = None


# ------------------------------
# Utility Functions
# ------------------------------

def get_pid_using_port_psutil(port: Union[str, int]):
    """Recover the PID of the process using a given port."""
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        # Listing connections of other users requires elevated
        # privileges on some systems (macOS, some Windows setups)
        return -1
    for con in connections:
        if con.laddr and con.laddr.port == port:
            return con.pid
        if con.raddr and con.raddr.port == port:
            return con.pid
    return -1


def get_pid_using_port_osx(port: Union[str, int]):
    try:
        out = subprocess.check_output(['lsof', '-ti', f':{port}'])
        pids = [int(p) for p in out.decode().split()]
        return pids[0] if pids else -1
    except subprocess.CalledProcessError:
        return -1


def get_pid_using_port(port: Union[str, int]):
    if platform.system() == "Darwin":
        return get_pid_using_port_osx(port)
    return get_pid_using_port_psutil(port)

def is_port_open(port: Union[str, int]):
    """True if something is listening on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def ping_server(timeout: float=0.5):
    """Return True if the server responds *in any way* to /connector/ping."""
    try:
        response = requests.get(PING_URL, timeout=timeout)
        return response.status_code is not None
    except requests.RequestException:
        return False


def normalize_url(url: str):
    """Clean common copy/paste mistakes before sending a URL to Zotero."""
    url = url.strip()
    match = ARXIV_PDF_URL_RE.match(url)
    if match:
        cleaned = f"https://arxiv.org/abs/{match.group(1)}"
        print(f"ℹ️ Normalized arXiv URL: {url} -> {cleaned}")
        return cleaned
    match = CVF_PDF_URL_RE.match(url)
    if match:
        cleaned = f"{match.group(1)}/html/{match.group(2)}.html"
        print(f"ℹ️ Normalized CVF URL: {url} -> {cleaned}")
        return cleaned
    match = ARXIV_ABS_URL_RE.match(url)
    if match:
        cleaned = match.group(1)
        print(f"ℹ️ Normalized arXiv URL: {url} -> {cleaned}")
        return cleaned
    return url


def node_executable():
    """Return a Node executable from PATH or the active Python environment."""
    executable = shutil.which('node')
    if executable:
        return executable

    # Fall back to a Node shipped inside the active Python environment.
    # Conda puts it in `<prefix>/bin` on Unix, and in `<prefix>` or
    # `<prefix>/Scripts` on Windows
    prefix = Path(sys.prefix)
    candidates = [prefix / 'Scripts' / 'node.exe', prefix / 'node.exe'] \
        if IS_WINDOWS else [prefix / 'bin' / 'node']
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return 'node'


# ------------------------------
# Server Lifecycle Management
# ------------------------------

def start_server(patience: float=30, timestep: float=0.25):
    """
    Start the translation server only if not already running.
    Wait until it's *actually responding*, not just bound to port.
    """
    global _translation_process

    if is_port_open(SERVER_PORT):
        print(f"ℹ️ Translation server already running on port {SERVER_PORT}.")
        return

    print(f"🔄 Starting translation server on port {SERVER_PORT}...")

    # Kill any stale process bound to port before launching new
    kill_pid_using_port(SERVER_PORT)

    # Check that the node version is at most 20
    check_node_version()

    server_path = Path(__file__).resolve().parent.parent / 'translation_server'
    server_script = server_path / 'src' / 'server.js'
    if not server_script.exists():
        print(
            f"❌ Could not find the translation server at {server_script}.\n"
            f"👉 Reinstall NoRA, or run `npm install` in {server_path}.")
        sys.exit(1)

    # Run the server in its own process group, so that it can be killed
    # along with its children. Windows has no process groups in the POSIX
    # sense and uses creation flags instead
    if IS_WINDOWS:
        group_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group_kwargs = {"start_new_session": True}

    _translation_process = subprocess.Popen(
        [node_executable(), str(server_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(server_path),
        **group_kwargs
    )

    # Wait for port binding + ping success
    start = time.time()
    print("⏳ Waiting for server to become ready", end="", flush=True)
    while time.time() - start < patience:
        if is_port_open(SERVER_PORT):
            print("\n✅ Server is ready!")
            return
        print(".", end="", flush=True)
        time.sleep(timestep)

    if is_port_open(SERVER_PORT):
        print("\n✅ Server is ready!")
        return

    # Failed startup — stop the process before dumping logs so reads cannot hang.
    print("\n❌ Translation server failed to start. Logs:")
    try:
        _translation_process.terminate()
        stdout, stderr = _translation_process.communicate(timeout=5)
        print(stdout.decode())
        print(stderr.decode())
    except Exception:
        pass

    print("Failed to start translation server.")
    sys.exit(1)


def kill_process_tree(pid: int, patience: float=5):
    """Terminate a process and all its children, on any platform."""
    try:
        parent = psutil.Process(pid)
    except (psutil.NoSuchProcess, ValueError):
        return

    try:
        processes = parent.children(recursive=True)
    except psutil.Error:
        processes = []
    processes.append(parent)

    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _, alive = psutil.wait_procs(processes, timeout=patience)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def kill_server():
    """Kill the server and all children properly."""
    global _translation_process
    if _translation_process is None:
        return

    kill_process_tree(_translation_process.pid)

    # Cleanup lingering socket holders
    kill_pid_using_port(SERVER_PORT)
    _translation_process = None


def kill_pid_using_port(port: Union[str, int], patience: float=5, timestep: float=0.1):
    pid = get_pid_using_port(port)
    if pid is None or pid <= 0:
        return  # nothing to kill

    kill_process_tree(pid, patience=patience)

    start = time.time()
    while True:
        pid = get_pid_using_port(port)
        if pid is None or pid <= 0 or time.time() - start > patience:
            break
        time.sleep(timestep)


def safe_kill_server():
    try:
        kill_server()
    except Exception:
        pass

# Automatically shut down at program exit
atexit.register(safe_kill_server)


# ------------------------------
# Data & Translation Functions
# ------------------------------

def json_to_python(data: Union[str, List, Dict]):
    """Same conversion helper you had — unchanged."""
    try:
        data = data.decode()
    except (UnicodeDecodeError, AttributeError):
        pass

    try:
        data = json.loads(data)
    except:
        pass

    if isinstance(data, str):
        print(f"❌ The translation server returned an error message:\n{data}")
        sys.exit(1)

    if isinstance(data, list):
        if isinstance(data[0], dict):
            data = data[0]
        else:
            print(
                f"❌ Expected a List(Dict), but received a "
                f"List({type(data[0])}):\n{data}")
            sys.exit(1)
    elif not isinstance(data, dict):
        print(f"❌ Expected a Dict or a List(Dict), got a {type(data)}:\n{data}")
        sys.exit(1)

    if 'data' not in data:
        data['data'] = {**data}

    print("✅ Metadata retrieved successfully!")

    return data


def translate_from_url(url: str, timeout: float=20):
    url = normalize_url(url)
    start_server()
    print(f"ℹ️ Retrieving metadata for URL: {url} ...")
    try:
        response = requests.post(
            f"{SERVER_IP}/web",
            data=url,
            headers={"Content-Type": "text/plain"},
            timeout=timeout)
        response.raise_for_status()
        out = response.text
    except requests.RequestException as error:
        print(f"❌ Failed to contact the translation server: {error}")
        print("❌ Failed to contact the translation server. Please check your internet connection or try again.")
        sys.exit(1)
    return json_to_python(out)


def translate_from_identifier(identifier: str, timeout: float=20):
    start_server()
    print(f"ℹ️ Retrieving metadata for identifier: {identifier} ...")
    try:
        response = requests.post(
            f"{SERVER_IP}/search",
            data=identifier,
            headers={"Content-Type": "text/plain"},
            timeout=timeout)
        response.raise_for_status()
        out = response.text
    except requests.RequestException as error:
        print(f"❌ Failed to contact the translation server: {error}")
        print("❌ Failed to contact the translation server. Please check your internet connection or try again.")
        sys.exit(1)
    return json_to_python(out)


def check_node_version():
    output = subprocess.check_output([node_executable(), "-v"]).decode().strip()
    major = int(output.replace('v', '').split(".")[0])
    if major < 20:
        print(f"⚠️ Detected Node {major}. Please use Node 20.x or newer for compatibility.")
