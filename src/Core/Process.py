"""
Core/Process.py
Child-process lifecycle and AppImage mounting.

Emulator-agnostic. Guarantees the emulator dies with the launcher on every
platform:

    Windows : Job Object with KILL_ON_JOB_CLOSE
    Linux   : PR_SET_PDEATHSIG -> SIGKILL
    macOS   : SIGHUP restored to default in the child
"""

import ctypes
import os
import subprocess
import sys

from .Log import log, fatal

# ============================================================================
# WIN32 JOB OBJECT
# ============================================================================
_win32_job       = None
_win32_job_ready = False


def _create_win32_job():
    """Job Object with KILL_ON_JOB_CLOSE - OS kills the emulator if launcher dies."""
    if sys.platform != "win32":
        return None

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.windll.kernel32
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None

    # Set up the extended limit info struct
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    # Set the information (Class 9 = JobObjectExtendedLimitInformation)
    result = k32.SetInformationJobObject(
        job,
        9,
        ctypes.byref(info),
        ctypes.sizeof(info)
    )

    if not result:
        err = k32.GetLastError()
        log("WARNING", "SetInformationJobObject failed", f"error={err}")
        return None

    log("INFO", "Win32 Job Object created (KILL_ON_JOB_CLOSE)")
    return job


def _job():
    """Create the Job Object on first use (so its log line reaches the log file)."""
    global _win32_job, _win32_job_ready
    if not _win32_job_ready:
        _win32_job_ready = True
        _win32_job = _create_win32_job()
    return _win32_job


# ============================================================================
# CHILD ENVIRONMENT
# ============================================================================
# Variables PyInstaller's bootloader and its runtime hooks set for the
# launcher itself; the emulator must not see them
_FROZEN_VARS = ("TCL_LIBRARY", "TK_LIBRARY", "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR",
                "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC")
_LIBRARY_PATHS = ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")


def clean_child_env(env, bundle_dir=None):
    """
    The environment for the emulator without the launcher's bundle in it.

    A PyInstaller one-file exe unpacks its Python, Tcl/Tk, SDL and C runtime
    libraries into a temp folder (sys._MEIPASS) and points the library
    search path at it (LD_LIBRARY_PATH on Linux, keeping the user's value in
    LD_LIBRARY_PATH_ORIG). Inherited by the emulator, that folder would feed
    it the launcher's older copies of libraries it ships or expects from the
    system - and a mismatched C/C++ runtime can stop a game from booting.
    """
    env = dict(env)
    bundle_dir = bundle_dir if bundle_dir is not None else getattr(sys, "_MEIPASS", None)
    for name in _FROZEN_VARS:
        env.pop(name, None)
    if "LD_LIBRARY_PATH_ORIG" in env:
        original = env.pop("LD_LIBRARY_PATH_ORIG")
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if bundle_dir:
        root = os.path.normcase(os.path.abspath(bundle_dir))
        for name in _LIBRARY_PATHS:
            if not env.get(name):
                continue
            kept = [entry for entry in env[name].split(os.pathsep) if entry and not
                    os.path.normcase(os.path.abspath(entry)).startswith(root)]
            if kept:
                env[name] = os.pathsep.join(kept)
            else:
                env.pop(name)
    return env


def _set_dll_directory(path):
    """SetDllDirectoryW: None restores Windows' default DLL search order."""
    try:
        ctypes.windll.kernel32.SetDllDirectoryW(path)
    except Exception as e:
        log("WARNING", "SetDllDirectoryW failed", e)


# ============================================================================
# LAUNCH
# ============================================================================
def launch(cmd_args, env):
    """
    Start the emulator, tied to the launcher's lifetime.

    Args:
        cmd_args (list[str]): Full command line, executable first.
        env      (dict):      Environment for the child process.

    Returns:
        subprocess.Popen: The running emulator process.
    """
    if sys.platform == "win32":
        # The bootloader pointed the DLL search path at the launcher's bundle,
        # and child processes inherit it: the emulator would load the
        # launcher's VCRUNTIME140.dll, SDL3.dll... instead of its own or the
        # system's. Restore the default search order just for this
        # CreateProcess (the launcher still loads its own DLLs afterwards).
        bundle_dir = getattr(sys, "_MEIPASS", None)
        env = clean_child_env(env, bundle_dir)
        if bundle_dir:
            _set_dll_directory(None)
        try:
            # CREATE_SUSPENDED so the process is attached to the Job Object
            # before it can spawn anything of its own
            process = subprocess.Popen(cmd_args, env=env, creationflags=0x00000004)
        finally:
            if bundle_dir:
                _set_dll_directory(bundle_dir)
        job = _job()
        if job:
            result = ctypes.windll.kernel32.AssignProcessToJobObject(
                job, int(process._handle)
            )
            if result:
                log("INFO", "Emulator assigned to Job Object")
            else:
                err = ctypes.windll.kernel32.GetLastError()
                log("WARNING", "AssignProcessToJobObject failed", f"error={err}")
        ctypes.windll.ntdll.NtResumeProcess(int(process._handle))
    else:
        import signal as _signal
        if sys.platform == "darwin":
            _preexec = lambda: _signal.signal(_signal.SIGHUP, _signal.SIG_DFL)
        else:
            _libc = ctypes.CDLL("libc.so.6", use_errno=True)
            _preexec = lambda: _libc.prctl(1, _signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG
        process = subprocess.Popen(cmd_args, env=clean_child_env(env), preexec_fn=_preexec)
    return process


# ============================================================================
# CLOSING
# ============================================================================
WM_CLOSE = 0x0010


def request_close(process):
    """
    Ask the emulator to close the way its own window's X button does, so it
    stops the game and saves its settings before exiting. Returns at once;
    the caller waits for the process and kills it if it does not go.

    Windows : WM_CLOSE posted to the process's visible top-level windows
    Others  : SIGTERM

    Returns:
        bool: True if the request could be delivered.
    """
    if process is None or process.poll() is not None:
        return False
    if sys.platform != "win32":
        try:
            process.terminate()
            return True
        except OSError:
            return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        found = []

        @enum_proc
        def each(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and user32.IsWindowVisible(hwnd) \
                    and not user32.GetWindow(hwnd, 4):          # GW_OWNER: skip owned dialogs
                found.append(hwnd)
            return True

        user32.EnumWindows(each, 0)
        for hwnd in found:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return bool(found)
    except Exception as e:
        log("WARNING", "Could not ask the emulator to close", e)
        return False


# ============================================================================
# THE EMULATOR'S DIALOGS
# ============================================================================
# A dialog (Eden's "Game Requires Firmware", an error...) needs a mouse: the
# launcher finds it, asks the players and answers it with Enter or Escape.
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101


def pick_dialogs(windows, main_names):
    """
    The dialogs among an emulator's visible top-level windows.

    Args:
        windows (list): [(hwnd, title, owned)] - owned: it has an owner window.
        main_names (list[str]): lower-case words in its main windows' titles
                                ("eden" for "Eden | v0.2.1 | Mario Kart 8 Deluxe").

    Returns:
        list: [(hwnd, title)] of the windows owned by another one, or titled
              without the emulator's name (an untitled window is not one).
    """
    found = []
    for hwnd, title, owned in windows:
        title = (title or "").strip()
        if not title:
            continue
        lowered = title.lower()
        if owned or not any(name in lowered for name in main_names):
            found.append((hwnd, title))
    return found


def _process_windows(pid):
    """[(hwnd, title, owned)] of a process's visible top-level windows (Windows)."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []

    @enum_proc
    def each(hwnd, _):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, 256)
            found.append((int(hwnd), buffer.value, bool(user32.GetWindow(hwnd, 4))))   # GW_OWNER
        return True

    user32.EnumWindows(each, 0)
    return found


def find_dialogs(process, main_names):
    """The emulator's open dialogs: [(hwnd, title)] (always [] outside Windows)."""
    if sys.platform != "win32" or process is None or process.poll() is not None:
        return []
    try:
        return pick_dialogs(_process_windows(process.pid), main_names)
    except Exception as e:
        log("WARNING", "Could not list the emulator's windows", e)
        return []


def dialog_open(hwnd):
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def answer_dialog(hwnd, accept):
    """
    Answer a dialog as its keyboard would: Enter (its default button, OK)
    or Escape (Cancel). It is brought to the front first.
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        key = VK_RETURN if accept else VK_ESCAPE
        scan = 0x1C if accept else 0x01
        user32.SetForegroundWindow(hwnd)
        user32.PostMessageW(hwnd, WM_KEYDOWN, key, 1 | (scan << 16))
        user32.PostMessageW(hwnd, WM_KEYUP, key, 1 | (scan << 16) | (1 << 30) | (1 << 31))
        return True
    except Exception as e:
        log("WARNING", "Could not answer the emulator's dialog", e)
        return False


# ============================================================================
# THE GAME ON SCREEN
# ============================================================================
# Eden shows its own loading screen (the game's name, "Loading shaders...")
# while the game's picture stays hidden: the render window is shown only
# once the loading screen has gone, i.e. the game drew its first frame
# (yuzu/main_window.cpp, LoadingScreen::Hidden -> render_window->show()).
# The picture is a native window (bootmanager.cpp, RenderWidget with
# WA_NativeWindow) and the only child of the render window; Qt may make
# the render window's siblings - the loading screen, the game list - native
# windows too, but none of those is the only child of its parent.
RENDER_SHARE = 0.35             # of the main window's area, a child window this big can be the game's picture


def render_shown(tops, descendants):
    """
    True once the emulator's game picture is on screen.

    Args:
        tops (list): [(hwnd, width, height)] of its visible top-level windows.
        descendants (dict): {top hwnd: [(hwnd, parent hwnd, visible, width, height)]}
                            of every window inside each one.
    """
    for top, w, h in tops:
        inside = descendants.get(top, [])
        children = {}
        for hwnd, parent, _visible, _w, _h in inside:
            children.setdefault(parent, []).append(hwnd)
        for hwnd, parent, visible, cw, ch in inside:
            if visible and cw * ch >= RENDER_SHARE * w * h and hwnd not in children \
                    and len(children.get(parent, ())) == 1:
                return True
    return False


def can_watch_windows():
    """True where the launcher can see the emulator's windows (Windows)."""
    return sys.platform == "win32"


def _window_size(user32, hwnd, client=False):
    from ctypes import wintypes
    rect = wintypes.RECT()
    (user32.GetClientRect if client else user32.GetWindowRect)(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def game_shown(process):
    """True once the game's picture is on screen (always False outside Windows)."""
    if sys.platform != "win32" or process is None or process.poll() is not None:
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        tops, descendants = [], {}
        for hwnd, _title, owned in _process_windows(process.pid):
            if owned:
                continue
            w, h = _window_size(user32, hwnd, client=True)
            tops.append((hwnd, w, h))
            found = descendants[hwnd] = []

            @enum_proc
            def each(child, _):
                cw, ch = _window_size(user32, child)
                parent = user32.GetAncestor(child, 1)           # GA_PARENT
                found.append((int(child), int(parent or 0), bool(user32.IsWindowVisible(child)), cw, ch))
                return True
            user32.EnumChildWindows(hwnd, each, 0)
        return render_shown(tops, descendants)
    except Exception as e:
        log("WARNING", "Could not look at the emulator's windows", e)
        return False


def bring_to_front(process):
    """Give the emulator's largest window the keyboard and the front (Windows)."""
    if sys.platform != "win32" or process is None:
        return
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        windows = [(hwnd, _window_size(user32, hwnd)) for hwnd, _t, owned in _process_windows(process.pid)
                   if not owned]
        if windows:
            hwnd = max(windows, key=lambda item: item[1][0] * item[1][1])[0]
            user32.SetForegroundWindow(hwnd)
    except Exception as e:
        log("WARNING", "Could not bring the emulator to the front", e)


# ============================================================================
# APPIMAGE MOUNT HELPERS (LINUX ONLY)
# ============================================================================
_mount_proc = None  # Popen handle - terminating it unmounts the squashfs


def mount_appimage(appimage_path):
    """
    Mount an AppImage using --appimage-mount and keep it mounted for the
    whole session. PR_SET_PDEATHSIG ensures the mount process is killed even
    on a hard launcher crash.

    Returns:
        str: The mount point (squashfs root). Callers usually append usr/bin.
    """
    global _mount_proc

    import signal
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PR_SET_PDEATHSIG = 1

    _mount_proc = subprocess.Popen(
        [appimage_path, "--appimage-mount"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=clean_child_env(os.environ),
        preexec_fn=lambda: libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    )

    mount_point = _mount_proc.stdout.readline().decode().strip()

    if not mount_point or not os.path.exists(mount_point):
        fatal(
            "AppImage Mount Failed",
            f"Could not mount {os.path.basename(appimage_path)}.\n\n"
            f"Please ensure the file is executable:\n"
            f"chmod +x {appimage_path}",
            "AppImage mount failed", appimage_path
        )

    log("INFO", "AppImage detected", appimage_path)
    log("INFO", "AppImage mounted at", mount_point)
    return mount_point


def unmount_appimage():
    """Terminate the mount process, releasing the squashfs mount. No-op if unmounted."""
    global _mount_proc

    if not _mount_proc:
        return

    _mount_proc.terminate()
    _mount_proc = None
    log("INFO", "AppImage unmounted")
