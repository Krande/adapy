import os

from ada.core.utils import download_to, unzip_it


def download_tool(url, download_path):
    os.makedirs(download_path.parent, exist_ok=True)
    download_to(download_path, url)
    unzip_it(download_path, download_path.parent)


def attach_vs_debugger_to_this_process():
    # get the current process id
    pid = os.getpid()
    # attach the debugger from a running visual studio instance (with the relevant project loaded) to this process
    os.system(f"vsjitdebugger -p {pid}")
