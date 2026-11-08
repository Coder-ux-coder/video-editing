"""Child processes that never outlive an interrupted command.

FFmpeg and the Remotion renderer (Node, which starts Chrome) run in their own process group, so
an interrupted or failed command stops the whole tree, not just the first process. Otherwise a
render resumed after Ctrl-C could race an orphaned FFmpeg still writing the same file.
"""

from __future__ import annotations

import os
import signal
import subprocess


def popen_group(cmd: list[str], **kw) -> subprocess.Popen:
    if os.name == "posix":
        kw["start_new_session"] = True
    else:                                                     # Windows
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, **kw)


def stop_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started; wait until it is gone."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
