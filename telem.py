# -*- coding: utf-8 -*-
# Copyright (c) 2025, Sky Net Reporting
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
author: Sky Net Systems
name: skynet_reporting
type: notification
short_description: Create detailed, best-practice traces for Ansible runs via OpenTelemetry.
version_added: 1.0.0
description:
  - This callback creates a distinct span for each task on each host, using a curated set of attributes
    based on OpenTelemetry semantic conventions for clear and immediate observability.
  - It automatically uses the system's CA trust store for secure OTLP/gRPC communication.
options:
  endpoint:
    type: str
    description: The OTLP endpoint (e.g., "https://collector.internal:4317").
    env: [OTEL_EXPORTER_OTLP_ENDPOINT]
    ini:
      - section: callback_skynet_reporting
        key: endpoint
  neuron_team:
    type: str
    description: The team responsible for this playbook run, used for service naming.
    env: [NEURON_TEAM]
    ini:
      - section: callback_skynet_reporting
        key: neuron_team
  neuron_app:
    type: str
    description: The application this playbook targets, used for service naming.
    env: [NEURON_APP]
    ini:
      - section: callback_skynet_reporting
        key: neuron_app
  traceparent:
    type: str
    description: The W3C Trace Context header (traceparent) to link this playbook run to a parent trace.
    env: [TRACEPARENT]
  enable_debug_logging:
    default: false
    type: bool
    description: Enable verbose logging to the Ansible console for debugging the callback itself.
    env: [ANSIBLE_SKYNET_DEBUG_LOGGING]
    ini:
      - section: callback_skynet_reporting
        key: enable_debug_logging
requirements:
  - opentelemetry-api
  - opentelemetry-sdk
  - opentelemetry-exporter-otlp
  - grpcio
"""

EXAMPLES = r"""
# --- ansible.cfg ---
[defaults]
# The path must match where you place the skynet_reporting.py file
callback_plugins   = /path/to/callback_plugins
callbacks_enabled = skynet_reporting

[callback_skynet_reporting]
# Configure the connection and metadata directly in the config
endpoint = https://collector.internal:4317
neuron_team = teamA
neuron_app = appZ
enable_debug_logging = true

# --- Environment Variables ---
# To link this run to an orchestrator's trace, set the TRACEPARENT.
# Your orchestrator (e.g., Jenkins, another Ansible playbook) would generate this.
export TRACEPARENT="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
"""

import os
import ssl
from os.path import basename

from ansible.errors import AnsibleError
from ansible.module_utils.ansible_release import __version__ as ansible_version
from ansible.plugins.callback import CallbackBase

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCSpanExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace.status import Status, StatusCode
    HAS_OTEL = True
except ImportError as e:
    HAS_OTEL = False
    OTEL_IMPORT_ERROR = e


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'skynet_reporting' # Updated name
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, display=None):
        super(CallbackModule, self).__init__(display=display)
        if not HAS_OTEL:
            raise AnsibleError(f'The opentelemetry libraries must be installed. Error: {OTEL_IMPORT_ERROR}')

        self.tracer = None
        self.tracer_provider = None
        self.playbook_span = None
        self.errors_in_playbook = 0
        self.debug_enabled = False

    def _debug(self, msg):
        if self.debug_enabled:
            self._display.v(f"[{self.CALLBACK_NAME}] {msg}")

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(task_keys, var_options, direct)
        self.debug_enabled = self.get_option('enable_debug_logging')
        self._debug("Options loaded.")

    def _init_otel(self):
        if self.tracer: return

        team = self.get_option('neuron_team') or "unknown_team"
        app = self.get_option('neuron_app') or "unknown_app"
        service_name = f"ansible.skynet.{team}.{app}"

        self._debug(f"Initializing OpenTelemetry SDK for service: {service_name}")
        resource = Resource.create({SERVICE_NAME: service_name})
        self.tracer_provider = TracerProvider(resource=resource)
        protocol = os.getenv('OTEL_EXPORTER_OTLP_TRACES_PROTOCOL', 'grpc')
        endpoint = self.get_option('endpoint')

        if not endpoint:
            self._display.warning("OTLP endpoint is not set. Traces will not be sent.")
            return

        self._debug(f"Using OTLP protocol: {protocol} with endpoint: {endpoint}")
        exporter = None
        if protocol == 'grpc':
            credentials = ssl.create_default_context()
            exporter = GRPCSpanExporter(endpoint=endpoint, credentials=credentials)
        elif protocol == 'http/protobuf':
            exporter = HTTPSpanExporter(endpoint=endpoint)

        if exporter:
            processor = BatchSpanProcessor(exporter)
            self.tracer_provider.add_span_processor(processor)
            trace.set_tracer_provider(self.tracer_provider)
            self.tracer = trace.get_tracer(self.CALLBACK_NAME, ansible_version)
            self._debug("Tracer initialized successfully.")
        else:
            self._display.warning(f"Protocol '{protocol}' not supported. Traces will not be sent.")

    def v2_playbook_on_start(self, playbook):
        self._init_otel()
        if not self.tracer: return

        playbook_name = basename(playbook._file_name)
        self._debug(f"Starting trace for playbook: {playbook_name}")
        traceparent = self.get_option('traceparent')
        parent_context = TraceContextTextMapPropagator().extract({'traceparent': traceparent}) if traceparent else None

        self.playbook_span = self.tracer.start_span(
            name=f"playbook: {playbook_name}", kind=SpanKind.SERVER, context=parent_context
        )
        self.playbook_span.set_attribute("ansible.playbook.name", playbook_name)
        self.playbook_span.set_attribute("neuron.team", self.get_option('neuron_team'))
        self.playbook_span.set_attribute("neuron.app", self.get_option('neuron_app'))
        self.playbook_span.set_attribute("ansible.version", ansible_version)

    def _create_task_result_span(self, result, status_string: str):
        if not self.playbook_span: return

        task = result._task
        host = result._host
        span_name = f"{task.get_name()} on {host.get_name()}"
        self._debug(f"Creating span for result: {span_name}")

        parent_context = trace.set_span_in_context(self.playbook_span)
        span = self.tracer.start_span(name=span_name, context=parent_context)

        # -- Curated "Best Practice" Attributes --
        span.set_attribute("host.name", host.get_name())
        span.set_attribute("code.function", task.action)
        span.set_attribute("code.filepath", task.get_path())
        span.set_attribute("ansible.task.name", task.get_name())
        span.set_attribute("ansible.task.status", status_string)
        if 'changed' in result._result:
            span.set_attribute("ansible.result.changed", result._result['changed'])

        status_code = StatusCode.OK
        if status_string == "failed":
            self.errors_in_playbook += 1
            status_code = StatusCode.ERROR
            span.set_attribute("error", True)
            msg = result._result.get('msg', 'Task failed without a specific message.')
            span.record_exception(Exception(msg))

        span.set_status(Status(status_code))
        span.end()

    def v2_runner_on_ok(self, result):
        self._create_task_result_span(result, "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._create_task_result_span(result, "ignored" if ignore_errors else "failed")

    def v2_runner_on_skipped(self, result):
        self._create_task_result_span(result, "skipped")

    def v2_playbook_on_stats(self, stats):
        if not self.playbook_span: return

        self._debug("Playbook finished. Ending root span.")
        if self.errors_in_playbook > 0:
            self.playbook_span.set_status(Status(StatusCode.ERROR, f"{self.errors_in_playbook} tasks failed."))
        else:
            self.playbook_span.set_status(Status(StatusCode.OK))
        self.playbook_span.end()

        self._debug("Forcing flush of all spans before exit.")
        self.tracer_provider.force_flush()
        self._debug("Flush complete.")