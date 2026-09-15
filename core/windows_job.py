from __future__ import annotations

import ctypes as c
import threading


class _BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", c.c_uint32),
                ("min_working_set", c.c_size_t), ("max_working_set", c.c_size_t),
                ("process_limit", c.c_uint32), ("affinity", c.c_size_t),
                ("priority", c.c_uint32), ("scheduling", c.c_uint32)]


class _ExtendedLimits(c.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", c.c_uint64 * 6),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]


class _ThreadEntry(c.Structure):
    _fields_ = [("size", c.c_uint32), ("usage", c.c_uint32), ("thread_id", c.c_uint32),
                ("process_id", c.c_uint32), ("base_priority", c.c_int32),
                ("delta_priority", c.c_int32), ("flags", c.c_uint32)]


class WindowsJob:
    """Unnamed, non-inherited kill-on-close job for one command and its descendants."""

    def __init__(self):
        self._lock = threading.Lock()
        self.api = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, c.c_wchar_p], c.c_void_p),
            "SetInformationJobObject": ([c.c_void_p, c.c_int, c.c_void_p, c.c_uint32], c.c_int),
            "AssignProcessToJobObject": ([c.c_void_p, c.c_void_p], c.c_int),
            "TerminateJobObject": ([c.c_void_p, c.c_uint32], c.c_int),
            "CloseHandle": ([c.c_void_p], c.c_int),
            "CreateToolhelp32Snapshot": ([c.c_uint32, c.c_uint32], c.c_void_p),
            "Thread32First": ([c.c_void_p, c.POINTER(_ThreadEntry)], c.c_int),
            "Thread32Next": ([c.c_void_p, c.POINTER(_ThreadEntry)], c.c_int),
            "OpenThread": ([c.c_uint32, c.c_int, c.c_uint32], c.c_void_p),
            "ResumeThread": ([c.c_void_p], c.c_uint32),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway permission.
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process) -> None:
        # Popen closes its primary-thread handle. Recover that handle through the
        # documented Toolhelp thread snapshot while the new process remains suspended.
        with self._lock:
            if not self.handle or not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise c.WinError(c.get_last_error())
            snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
            if snapshot == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            try:
                entry = _ThreadEntry()
                entry.size = c.sizeof(entry)
                found = self.api.Thread32First(snapshot, c.byref(entry))
                while found:
                    if entry.process_id == process.pid:
                        thread = self.api.OpenThread(2, False, entry.thread_id)
                        if not thread:
                            raise c.WinError(c.get_last_error())
                        try:
                            if self.api.ResumeThread(thread) != 1:
                                raise RuntimeError("Command primary thread did not resume from its expected suspended state")
                            return
                        finally:
                            self.api.CloseHandle(thread)
                    entry.size = c.sizeof(entry)
                    found = self.api.Thread32Next(snapshot, c.byref(entry))
                raise RuntimeError("Suspended command primary thread was not found")
            finally:
                self.api.CloseHandle(snapshot)

    def terminate(self) -> None:
        with self._lock:
            if self.handle and not self.api.TerminateJobObject(self.handle, 130):
                raise c.WinError(c.get_last_error())

    def close(self) -> None:
        with self._lock:
            handle, self.handle = self.handle, None
            if handle:
                self.api.CloseHandle(handle)
