import os
import sys
import json
import time
import psutil
import signal
import subprocess
import socket
import atexit
import platform
import re
from os.path import dirname
from typing import Union, List, Dict

__all__ = ['translate_from_url', 'translate_from_identifier']

SERVER_PORT = 1969
SERVER_IP = f"http://127.0.0.1:{SERVER_PORT}"
PING_URL = f"{SERVER_IP}/connector/ping"
ARXIV_ABS_URL_RE = re.compile(r"^(https?://arxiv\.org/abs/\d{4}\.\d{4,5})([A-Za-z])$")
ARXIV_PDF_URL_RE = re.compile(r"^https?://arxiv\.org/pdf/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?$", re.IGNORECASE)
CVF_PDF_URL_RE = re.compile(r"^(https?://openaccess\.thecvf\.com/content/[^/]+)/papers/(.+)\.pdf$", re.IGNORECASE)

# Global server process (singleton pattern)
_translation_process = None


# ------------------------------
# Utility Functions
# ------------------------------

def get_pid_using_port_unix(port: Union[str, int]):
    """Recover the PID of the process using a given port."""
    for con in psutil.net_connections():
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
    else:
        return get_pid_using_port_unix(port)

def is_port_open(port: Union[str, int]):
    """True if something is listening on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def ping_server(timeout: float=0.5):
    """Return True if the server responds *in any way* to /connector/ping."""
    try:
        out = subprocess.check_output([
            'curl', '-m', str(timeout), '-s', '-o', '/dev/null', '-w', '%{http_code}',
            PING_URL
        ])
        status = out.decode().strip()
        return status.isdigit()  # 200, 404, etc. — anything is good
    except subprocess.CalledProcessError:
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

    server_path = os.path.join(dirname(dirname(__file__)), 'translation_server')
    _translation_process = subprocess.Popen(
        ['node', 'src/server.js'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=server_path,
        preexec_fn=os.setsid
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


def kill_server():
    """Kill the server and all children properly."""
    global _translation_process
    if _translation_process is None:
        return

    # Kill process group
    try:
        os.killpg(os.getpgid(_translation_process.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass

    # Cleanup lingering socket holders
    kill_pid_using_port(SERVER_PORT)
    _translation_process = None


def kill_pid_using_port(port: Union[str, int], patience: float=5, timestep: float=0.1):
    pid = get_pid_using_port(port)
    if pid is None or pid <= 0:
        return  # nothing to kill

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

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
        out = subprocess.check_output([
            'curl', '-s', '-S', '-d', url, '-m', f'{timeout}', '-H', "Content-Type: text/plain", f"{SERVER_IP}/web"
        ])
    except subprocess.CalledProcessError:
        print("❌ Failed to contact the translation server. Please check your internet connection or try again.")
        sys.exit(1)
    return json_to_python(out)


def translate_from_identifier(identifier: str, timeout: float=20):
    start_server()
    print(f"ℹ️ Retrieving metadata for identifier: {identifier} ...")
    try:
        out = subprocess.check_output([
            'curl', '-s', '-S', '-d', identifier, '-m', f'{timeout}', '-H', "Content-Type: text/plain", f"{SERVER_IP}/search"
        ])
    except subprocess.CalledProcessError:
        print("❌ Failed to contact the translation server. Please check your internet connection or try again.")
        sys.exit(1)
    return json_to_python(out)


def check_node_version():
    output = subprocess.check_output(["node", "-v"]).decode().strip()
    major = int(output.replace('v', '').split(".")[0])
    if major < 20:
        print(f"⚠️ Detected Node {major}. Please use Node 20.x or newer for compatibility.")
