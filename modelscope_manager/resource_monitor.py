from __future__ import annotations

import ctypes
import gc
import os
import re
import time
from dataclasses import dataclass
from ctypes import wintypes


@dataclass(frozen=True)
class ResourceSample:
    cpu_percent: float
    working_set_bytes: int
    private_bytes: int
    gpu_dedicated_bytes: int | None
    gpu_shared_bytes: int | None


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _PdhValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", wintypes.LONG),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("wideStringValue", wintypes.LPWSTR),
    ]


class _PdhFormattedValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhValueUnion)]


class _PdhValueItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _PdhFormattedValue)]


def _instance_pid(instance_name: str) -> int | None:
    match = re.search(r"(?:^|_)pid_(\d+)(?:_|$)", instance_name, re.IGNORECASE)
    return int(match.group(1)) if match else None


class _GpuMemoryReader:
    PDH_MORE_DATA = 0x800007D2
    PDH_FMT_LARGE = 0x00000400

    def __init__(self, pid: int):
        self.pid = pid
        self.query = ctypes.c_void_p()
        self.dedicated = ctypes.c_void_p()
        self.shared = ctypes.c_void_p()
        self.available = False
        if os.name != "nt":
            return
        try:
            self.pdh = ctypes.WinDLL("pdh", use_last_error=True)
            self.pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
            self.pdh.PdhOpenQueryW.restype = wintypes.LONG
            self.pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
            self.pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
            self.pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
            self.pdh.PdhCollectQueryData.restype = wintypes.LONG
            self.pdh.PdhGetFormattedCounterArrayW.argtypes = [
                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
            ]
            self.pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
            self.pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
            self.pdh.PdhCloseQuery.restype = wintypes.LONG
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
                return
            paths = (
                (r"\GPU Process Memory(*)\Dedicated Usage", self.dedicated),
                (r"\GPU Process Memory(*)\Shared Usage", self.shared),
            )
            if any(self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)) != 0 for path, counter in paths):
                self.close()
                return
            self.available = self.pdh.PdhCollectQueryData(self.query) == 0
        except (AttributeError, OSError):
            self.close()

    def _read_counter(self, counter: ctypes.c_void_p) -> int | None:
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(
            counter, self.PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None,
        )
        if (status & 0xFFFFFFFF) != self.PDH_MORE_DATA or not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        status = self.pdh.PdhGetFormattedCounterArrayW(
            counter, self.PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer,
        )
        if status != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhValueItem))
        total = 0
        found = False
        for index in range(count.value):
            item = items[index]
            if item.szName and _instance_pid(item.szName) == self.pid and item.FmtValue.CStatus in {0, 1}:
                total += max(0, int(item.FmtValue.largeValue))
                found = True
        return total if found else 0

    def sample(self) -> tuple[int | None, int | None]:
        if not self.available or self.pdh.PdhCollectQueryData(self.query) != 0:
            return None, None
        return self._read_counter(self.dedicated), self._read_counter(self.shared)

    def close(self) -> None:
        query = getattr(self, "query", None)
        pdh = getattr(self, "pdh", None)
        if query and query.value and pdh is not None:
            pdh.PdhCloseQuery(query)
            query.value = None
        self.available = False


class ProcessResourceMonitor:
    def __init__(self, pid: int | None = None):
        self.pid = pid or os.getpid()
        if self.pid != os.getpid():
            raise ValueError("Resource monitor only supports the current process")
        self._last_wall = time.perf_counter()
        self._last_cpu = time.process_time()
        self._gpu = _GpuMemoryReader(self.pid)

    @staticmethod
    def _memory() -> tuple[int, int]:
        if os.name != "nt":
            return 0, 0
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0, 0
        return int(counters.WorkingSetSize), int(counters.PrivateUsage)

    def sample(self) -> ResourceSample:
        now_wall = time.perf_counter()
        now_cpu = time.process_time()
        wall_delta = max(0.000001, now_wall - self._last_wall)
        cpu_delta = max(0.0, now_cpu - self._last_cpu)
        self._last_wall = now_wall
        self._last_cpu = now_cpu
        cpu_percent = min(100.0, cpu_delta / wall_delta * 100.0 / max(1, os.cpu_count() or 1))
        working_set, private_bytes = self._memory()
        dedicated, shared = self._gpu.sample()
        return ResourceSample(cpu_percent, working_set, private_bytes, dedicated, shared)

    @staticmethod
    def trim_working_set() -> bool:
        gc.collect()
        if os.name != "nt":
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL
        return bool(psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()))

    def close(self) -> None:
        self._gpu.close()
