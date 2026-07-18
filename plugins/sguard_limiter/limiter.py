import ctypes
import os
import threading
import time
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll
advapi32 = ctypes.windll.advapi32

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Thread32First.restype = wintypes.BOOL
kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Thread32Next.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetPriorityClass.restype = wintypes.BOOL
kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, wintypes.INT]
kernel32.SetThreadPriority.restype = wintypes.BOOL
kernel32.SetThreadAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
kernel32.SetThreadAffinityMask.restype = ctypes.c_size_t
kernel32.SetThreadIdealProcessor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetThreadIdealProcessor.restype = wintypes.DWORD

ntdll.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
ntdll.NtSetInformationProcess.restype = wintypes.LONG

kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint]
kernel32.TerminateJobObject.restype = wintypes.BOOL

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_TERMINATE = 0x0001
THREAD_SET_INFORMATION = 0x0020
THREAD_QUERY_INFORMATION = 0x0040
THREAD_SET_LIMITED_INFORMATION = 0x0400
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
THREAD_PRIORITY_IDLE = -15
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
IDLE_PRIORITY_CLASS = 0x00000040
PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
ProcessIoPriority = 0x21
IoPriorityHintVeryLow = 0
JobObjectBasicLimitInformation = 2
JobObjectCpuRateControlInformation = 7
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00010000
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00020000

TARGET_EXE = "SGuard64.exe"


def _luid():
    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]
    return LUID()


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("ChildProcess", ctypes.c_size_t),
        ("This", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
    ]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]


class SGuardLimiterCore:
    def __init__(self, config):
        self._active_pids = set()
        self._job_map = {}
        self._last_apply = 0.0
        self._enabled = True
        self._running = False
        self._thread = None

        self._cpu_percent = config.get("cpu_percent", 5)
        self._monitor_interval = config.get("monitor_interval", 3.0)
        self._reapply_interval = config.get("reapply_interval", 30.0)

        self._on_status = None

    def set_status_callback(self, cb):
        self._on_status = cb

    def start(self):
        self._enabled = True
        self._running = True
        self._enable_debug_privilege()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._enabled = False
        for job in self._job_map.values():
            kernel32.CloseHandle(job)
        self._job_map.clear()
        self._active_pids.clear()

    def _enable_debug_privilege(self):
        h_token = wintypes.HANDLE()
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY = 0x0008
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(h_token),
        ):
            return
        try:
            luid = _luid()
            advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid))
            class LUID_AND_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("Luid", type(luid)), ("Attributes", wintypes.DWORD)]
            class TOKEN_PRIVILEGES(ctypes.Structure):
                _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = 0x00000002
            advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None)
        finally:
            kernel32.CloseHandle(h_token)

    def _find_sguard_processes(self):
        pids = []
        h = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h == wintypes.HANDLE(-1).value:
            return pids
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(h, ctypes.byref(pe))
            while ok:
                if pe.szExeFile.lower() == TARGET_EXE.lower():
                    pids.append(pe.th32ProcessID)
                ok = kernel32.Process32NextW(h, ctypes.byref(pe))
        finally:
            kernel32.CloseHandle(h)
        return pids

    def _try_open_process(self, pid):
        for access in [
            PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION | PROCESS_TERMINATE,
            PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION,
            PROCESS_SET_INFORMATION,
            PROCESS_QUERY_INFORMATION,
            0x1000,
        ]:
            h = kernel32.OpenProcess(access, False, pid)
            if h:
                return h, access
        return None, 0

    def _is_in_job(self, h_process):
        in_job = wintypes.BOOL(False)
        kernel32.IsProcessInJob(h_process, None, ctypes.byref(in_job))
        return in_job.value

    def _create_job_with_limits(self):
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        basic = JOBOBJECT_BASIC_LIMIT_INFORMATION()
        basic.PriorityClass = IDLE_PRIORITY_CLASS
        basic.LimitFlags = 0x00000004
        ok = kernel32.SetInformationJobObject(
            job, JobObjectBasicLimitInformation,
            ctypes.byref(basic), ctypes.sizeof(basic),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        cpu.CpuRate = int(self._cpu_percent * 100)
        kernel32.SetInformationJobObject(
            job, JobObjectCpuRateControlInformation,
            ctypes.byref(cpu), ctypes.sizeof(cpu),
        )
        return job

    def _apply_process_limits(self, h_process):
        applied = []
        ok = kernel32.SetPriorityClass(h_process, PROCESS_MODE_BACKGROUND_BEGIN)
        if ok:
            applied.append("background_mode")
        else:
            kernel32.SetPriorityClass(h_process, IDLE_PRIORITY_CLASS)
            applied.append("idle_priority")
            io_hint = ctypes.c_ulong(IoPriorityHintVeryLow)
            ret = ntdll.NtSetInformationProcess(
                h_process, ProcessIoPriority,
                ctypes.byref(io_hint), ctypes.sizeof(io_hint),
            )
            if ret >= 0:
                applied.append("io_priority")
        count = os.cpu_count() or 4
        mask = 1 << (count - 1)
        if kernel32.SetProcessAffinityMask(h_process, mask):
            applied.append("affinity_mask")
        return applied

    def _limit_threads(self, pid):
        h = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if h == wintypes.HANDLE(-1).value:
            return []
        threads = []
        try:
            te = THREADENTRY32()
            te.dwSize = ctypes.sizeof(THREADENTRY32)
            ok = kernel32.Thread32First(h, ctypes.byref(te))
            while ok:
                if te.th32OwnerProcessID == pid:
                    threads.append(te.th32ThreadID)
                ok = kernel32.Thread32Next(h, ctypes.byref(te))
        finally:
            kernel32.CloseHandle(h)
        count = os.cpu_count() or 4
        mask = 1 << (count - 1)
        set_ok = 0
        for tid in threads:
            for access in [THREAD_SET_LIMITED_INFORMATION, THREAD_SET_INFORMATION, THREAD_QUERY_LIMITED_INFORMATION]:
                ht = kernel32.OpenThread(access, False, tid)
                if not ht:
                    continue
                try:
                    if access & THREAD_SET_LIMITED_INFORMATION or access & THREAD_SET_INFORMATION:
                        if kernel32.SetThreadPriority(ht, THREAD_PRIORITY_IDLE):
                            set_ok += 1
                        kernel32.SetThreadAffinityMask(ht, mask)
                    break
                finally:
                    kernel32.CloseHandle(ht)
        return [f"threads_{set_ok}/{len(threads)}"]

    def _monitor_loop(self):
        while self._running:
            try:
                if self._enabled:
                    self._find_and_limit()
            except Exception:
                pass
            time.sleep(self._monitor_interval)

    def _find_and_limit(self):
        pids = self._find_sguard_processes()
        current_pids = set(pids)

        for pid in list(self._active_pids):
            if pid not in current_pids:
                self._active_pids.discard(pid)
                job = self._job_map.pop(pid, None)
                if job:
                    kernel32.CloseHandle(job)

        for pid in current_pids:
            if pid in self._active_pids:
                continue
            result = self._try_open_process(pid)
            if not result or not result[0]:
                continue
            h, access = result
            try:
                if not self._is_in_job(h):
                    job = self._create_job_with_limits()
                    if job:
                        assign_ok = kernel32.AssignProcessToJobObject(job, h)
                        if assign_ok:
                            self._job_map[pid] = job
                            self._active_pids.add(pid)
                            continue
                self._apply_process_limits(h)
            finally:
                kernel32.CloseHandle(h)
            self._limit_threads(pid)
            self._active_pids.add(pid)

        now = time.monotonic()
        if now - self._last_apply > self._reapply_interval:
            self._last_apply = now
            for pid in list(self._active_pids):
                if pid in self._job_map:
                    continue
                self._limit_threads(pid)

        if self._on_status:
            self._on_status({
                "running": True,
                "pids": list(self._active_pids),
            })
