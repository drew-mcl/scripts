#!/usr/bin/env python3
"""prom_push_callback.py
Ansible callback plugin that records high‑value release‑engineering metrics and
pushes them to a Prometheus Pushgateway once per playbook run, then deletes the
series so it does not linger.

Place this file in a directory listed in *ansible.cfg* under ``callback_plugins``
and add ``prom_push`` to ``callback_whitelist``.

Environment overrides
---------------------
* ``PROMETHEUS_PUSHGATEWAY`` – full URL, e.g. ``http://pushgw:9091``
* ``CB_PROM_PUSH_EXTRA_LABELS`` – comma‑separated ``key=value`` labels that will
  be added to every metric (example: ``env=prod,release_id=123``)

Python dependencies
-------------------
* ``prometheus-client`` ≥ 0.8.0

This plugin is intentionally self‑contained – no Ansible collections required.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import socket
import time
from datetime import datetime
from typing import Dict

from ansible.plugins.callback import CallbackBase
from ansible.utils.display import Display

# ---------------------------- optional dependency -----------------------------
try:
    from prometheus_client import (
        CollectorRegistry,
        Histogram,
        Gauge,
        Counter,
        push_to_gateway,
        delete_from_gateway,
    )

    HAS_PROM = True
except ImportError:
    HAS_PROM = False

# -----------------------------------------------------------------------------
_DISPLAY = Display()


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"  # single instance per playbook, collects all events
    CALLBACK_NAME = "prom_push"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super().__init__()

        if not HAS_PROM:
            self._disable_plugin("prometheus_client Python package missing –\n\n"
                                 "pip install prometheus-client")
            return

        # --------------------------- configuration ---------------------------
        self.gateway: str = os.getenv("PROMETHEUS_PUSHGATEWAY", "http://127.0.0.1:9091")
        self.extra_labels: Dict[str, str] = self._parse_extra_labels(
            os.getenv("CB_PROM_PUSH_EXTRA_LABELS", "")
        )

        # Stable job name; uniqueness comes from grouping_key labels.
        self.job_name = "ansible_release"

        # Create a private registry so concurrent playbook runs do not clash.
        self.registry = CollectorRegistry()

        # ---------------------------- metric defs ---------------------------
        # Deployment duration histogram (log‑style buckets up to 2 hours).
        self.m_duration = Histogram(
            "ansible_playbook_duration_seconds",
            "Wall‑clock runtime of the playbook.",
            ["playbook"],
            buckets=(30, 60, 120, 300, 900, 1800, 3600, 7200),
            registry=self.registry,
        )

        # Success(1)/Failure(0) gauge.
        self.m_result = Gauge(
            "ansible_playbook_success",
            "1 when the playbook completed without failed/unreachable hosts, else 0.",
            ["playbook"],
            registry=self.registry,
        )

        # How many hosts were touched.
        self.m_hosts = Gauge(
            "ansible_playbook_host_total",
            "Number of hosts processed by the playbook.",
            ["playbook"],
            registry=self.registry,
        )

        # Task outcome counters.
        self.m_task_status = Counter(
            "ansible_task_status_total",
            "Count of task outcomes per playbook run.",
            ["playbook", "status"],
            registry=self.registry,
        )

        _DISPLAY.v(f"Prometheus callback initialised → {self.gateway}")

    # ----------------------------- helpers ----------------------------------
    @staticmethod
    def _parse_extra_labels(raw: str) -> Dict[str, str]:
        labels: Dict[str, str] = {}
        for pair in (p.strip() for p in raw.split(",") if p.strip()):
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels[k.strip()] = v.strip()
        return labels

    def _push_metrics(self, labels: Dict[str, str]):
        """Push and then immediately delete the series to keep the Pushgateway tidy."""
        try:
            push_to_gateway(
                self.gateway,
                job=self.job_name,
                grouping_key=labels,
                registry=self.registry,
            )
            delete_from_gateway(self.gateway, job=self.job_name, grouping_key=labels)
            _DISPLAY.v(f"Prometheus metrics pushed for {labels}")
        except Exception as exc:  # noqa: BLE001
            _DISPLAY.warning(
                f"Prometheus callback – could not push metrics to {self.gateway}: {exc}"
            )

    # ---------------------------- event hooks -------------------------------
    def v2_playbook_on_start(self, playbook):
        self.playbook_name = os.path.basename(playbook._file_name)
        self.start_ts = time.time()
        _DISPLAY.v(f"prom_push: playbook '{self.playbook_name}' started")

    def v2_runner_on_ok(self, result):
        self.m_task_status.labels(playbook=self.playbook_name, status="ok").inc()

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if not ignore_errors:
            self.m_task_status.labels(playbook=self.playbook_name, status="failed").inc()

    def v2_runner_on_unreachable(self, result):
        self.m_task_status.labels(playbook=self.playbook_name, status="unreachable").inc()

    def v2_runner_on_skipped(self, result):
        self.m_task_status.labels(playbook=self.playbook_name, status="skipped").inc()

    def v2_playbook_on_stats(self, stats):
        duration = time.time() - self.start_ts
        self.m_duration.labels(playbook=self.playbook_name).observe(duration)

        processed = stats.processed.keys()  # hosts actually handled, respects --limit
        self.m_hosts.labels(playbook=self.playbook_name).set(len(processed))

        # Determine success (0 failed/unreachable hosts across the run)
        has_failure = any(
            stats.summarize(h)["failures"] > 0 or stats.summarize(h)["unreachable"] > 0
            for h in processed
        )
        self.m_result.labels(playbook=self.playbook_name).set(0 if has_failure else 1)

        labels = {
            "playbook": self.playbook_name,
            "controller": socket.gethostname(),
            **self.extra_labels,
        }

        _DISPLAY.v(
            f"prom_push: '{self.playbook_name}' finished in {duration:.1f}s – "
            f"{'FAILED' if has_failure else 'SUCCESS'}"
        )

        self._push_metrics(labels)
