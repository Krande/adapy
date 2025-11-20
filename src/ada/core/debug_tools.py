import os


def attach_vs_debugger_to_this_process():
    # get the current process id
    pid = os.getpid()
    # attach the debugger from a running visual studio instance (with the relevant project loaded) to this process
    os.system(f"vsjitdebugger -p {pid}")
