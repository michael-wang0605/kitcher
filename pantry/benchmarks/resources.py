"""Development-only sampled memory measurement with explicit scope and limits."""

import importlib.metadata
import json
import os
import threading
import time
from pathlib import Path


class ResourceSampler:
    def __init__(self, *, ollama_record=None, interval=0.1):
        try:
            import psutil
            import pynvml
        except ImportError as exc:
            raise RuntimeError(
                "Install requirements-dev.txt for measured recognition trials."
            ) from exc
        self.psutil, self.nvml = psutil, pynvml
        self.interval = interval
        self.roots = [psutil.Process(os.getpid())]
        if ollama_record:
            record = json.loads(Path(ollama_record).read_text(encoding="utf-8-sig"))
            process = psutil.Process(record["pid"])
            expected_time = (
                int(record["startedTicks"]) - 621355968000000000
            ) / 10000000
            if (
                abs(process.create_time() - expected_time) > 0.001
                or Path(process.exe()).resolve() != Path(record["executable"]).resolve()
            ):
                raise RuntimeError(
                    "Recorded Ollama process identity does not match its live process."
                )
            self.roots.append(process)
        self.stopping = threading.Event()
        self.thread = None
        self.processes = {}
        self.report = {
            "method": "psutil current working sets/private commit and NVML whole-board memory",
            "psutil": psutil.__version__,
            "nvidia_ml_py": importlib.metadata.version("nvidia-ml-py"),
            "requested_interval_seconds": interval,
            "samples": 0,
            "sample_gaps_seconds": [],
            "sampled_peak_working_set_sum_bytes": 0,
            "sampled_peak_private_commit_sum_bytes": 0,
            "gpu": [],
            "errors": [],
            "scope": "benchmark process and descendants; recorded Ollama process and descendants; whole GPU boards",
            "limitations": [
                "Sampled maxima can miss spikes between observations.",
                "Working-set sums can count shared pages more than once.",
                "GPU values include other applications; model allocation is a separate metric.",
                "Per-process OS peak fields are lifetime values, not peaks limited to this sample.",
            ],
        }
        self.previous = None
        self.gpu_handles = []
        self.nvml_started = False

    def __enter__(self):
        self.started = time.monotonic()
        try:
            self.nvml.nvmlInit()
            self.nvml_started = True
            for index in range(self.nvml.nvmlDeviceGetCount()):
                handle = self.nvml.nvmlDeviceGetHandleByIndex(index)
                name = self.nvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                self.gpu_handles.append(handle)
                self.report["gpu"].append(
                    {"index": index, "name": name, "sampled_peak_used_bytes": 0}
                )
        except self.nvml.NVMLError as exc:
            self.report["errors"].append("NVML unavailable: " + type(exc).__name__)
        try:
            self.sample()
            self.thread = threading.Thread(
                target=self.poll, name="recognition-resource-sampler", daemon=True
            )
            self.thread.start()
        except Exception:
            if self.nvml_started:
                self.nvml.nvmlShutdown()
                self.nvml_started = False
            raise
        return self

    def poll(self):
        while not self.stopping.wait(self.interval):
            try:
                self.sample()
            except Exception as exc:
                self.report["errors"].append(
                    "Memory sampling failed: " + type(exc).__name__
                )
                break

    def sample(self):
        now = time.monotonic()
        if self.previous is not None:
            self.report["sample_gaps_seconds"].append(round(now - self.previous, 6))
        self.previous = now
        selected = {}
        for root in self.roots:
            try:
                if root.is_running():
                    selected[root.pid] = root
                    for child in root.children(recursive=True):
                        selected[child.pid] = child
            except self.psutil.NoSuchProcess:
                pass
            except self.psutil.AccessDenied:
                self.report["errors"].append(
                    "Access denied when enumerating a monitored process tree."
                )
        resident, private = 0, 0
        private_available = True
        for process in selected.values():
            try:
                if not process.is_running():
                    continue
                fields = process.memory_info()._asdict()
                created = process.create_time()
                key = (process.pid, created)
                values = self.processes.setdefault(
                    key,
                    {
                        "pid": process.pid,
                        "created_unix": created,
                        "name": process.name(),
                        "executable": process.exe(),
                        "sampled_peak_working_set_bytes": 0,
                        "sampled_peak_private_commit_bytes": 0,
                    },
                )
                values["sampled_peak_working_set_bytes"] = max(
                    values["sampled_peak_working_set_bytes"], fields["rss"]
                )
                resident += fields["rss"]
                committed = fields.get("private")
                if committed is not None:
                    private += committed
                    values["sampled_peak_private_commit_bytes"] = max(
                        values["sampled_peak_private_commit_bytes"], committed
                    )
                else:
                    values["sampled_peak_private_commit_bytes"] = None
                    private_available = False
                for source, target in (
                    ("peak_wset", "lifetime_peak_working_set_bytes"),
                    ("peak_pagefile", "lifetime_peak_private_commit_bytes"),
                ):
                    if source in fields:
                        values[target] = max(values.get(target, 0), fields[source])
            except self.psutil.NoSuchProcess:
                pass
            except self.psutil.AccessDenied:
                self.report["errors"].append(
                    "Access denied reading a monitored process's memory."
                )
        self.report["sampled_peak_working_set_sum_bytes"] = max(
            self.report["sampled_peak_working_set_sum_bytes"], resident
        )
        if not private_available:
            self.report["sampled_peak_private_commit_sum_bytes"] = None
        elif self.report["sampled_peak_private_commit_sum_bytes"] is not None:
            self.report["sampled_peak_private_commit_sum_bytes"] = max(
                self.report["sampled_peak_private_commit_sum_bytes"], private
            )
        for index, handle in enumerate(self.gpu_handles):
            try:
                info = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                gpu = self.report["gpu"][index]
                gpu.setdefault("baseline_used_bytes", info.used)
                gpu["total_bytes"] = info.total
                gpu["sampled_peak_used_bytes"] = max(
                    gpu["sampled_peak_used_bytes"], info.used
                )
            except self.nvml.NVMLError as exc:
                self.report["errors"].append(
                    "NVML sample failed: " + type(exc).__name__
                )
        self.report["samples"] += 1

    def __exit__(self, *args):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.thread and self.thread.is_alive():
            self.report["errors"].append(
                "Memory sampler did not stop within five seconds."
            )
        else:
            try:
                self.sample()
            except Exception as exc:
                self.report["errors"].append(
                    "Final memory sample failed: " + type(exc).__name__
                )
        if self.nvml_started:
            self.nvml.nvmlShutdown()
        gaps = self.report.pop("sample_gaps_seconds")
        self.report["maximum_observed_gap_seconds"] = max(gaps, default=0)
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        self.report["processes"] = list(self.processes.values())
        self.report["errors"] = sorted(set(self.report["errors"]))
