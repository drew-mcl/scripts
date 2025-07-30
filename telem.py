# -*- coding: utf-8 -*-
# Copyright (c) 2021, Victor Martinez <VictorMartinezRubio@gmail.com>
# Copyright (c) 2025, Refactored for Simplicity and Customization
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
author: Victor Martinez (@v1v), Refactored by Gemini
name: opentelemetry
type: notification
short_description: Create detailed, customizable distributed traces with OpenTelemetry
version_added: 3.7.0
description:
  - This callback creates a distinct span for each task on each host, allowing for detailed performance analysis.
  - It is configured via standard OpenTelemetry environment variables and can be customized heavily in ansible.cfg.
  - It automatically uses the system's CA trust store for secure OTLP/gRPC communication.
options:
  otel_service_name:
    default: ansible
    type: str
    description: The service name resource attribute for the trace.
    env: [OTEL_SERVICE_NAME]
    ini:
      - section: callback_opentelemetry
        key: otel_service_name
  traceparent:
    type: str
    description: The W3C Trace Context header (traceparent) to continue a trace from a parent process.
    env: [TRACEPARENT]
  hide_task_arguments:
    default: false
    type: bool
    description: Hide task arguments from span attributes (if not overridden by `span_attributes`).
    env: [ANSIBLE_OPENTELEMETRY_HIDE_TASK_ARGUMENTS]
    ini:
      - section: callback_opentelemetry
        key: hide_task_arguments
  enable_debug_logging:
    default: false
    type: bool
    description: Enable verbose logging to the Ansible console for debugging the callback itself.
    env: [ANSIBLE_OPENTELEMETRY_DEBUG_LOGGING]
    ini:
      - section: callback_opentelemetry
        key: enable_debug_logging
  span_attributes:
    type: dict
    description:
      - A dictionary mapping desired span attribute names to data paths within the Ansible `result` object.
      - This gives you full control over the "schema" of your trace data.
      - Paths are dot-separated strings like `_host.name` or `_result.rc`.
      - If an attribute path is invalid for a given task, it will be silently ignored.
    ini:
      - section: callback_opentelemetry
        key: span_attributes
requirements:
  - opentelemetry-api
  - opentelemetry-sdk
  - opentelemetry-exporter-otlp
  - grpcio
"""

EXAMPLES = r"""
# --- ansible.cfg ---
[defaults]
callbacks_enabled = community.general.opentelemetry

[callback_opentelemetry]
# Enable debug logging for the callback itself
enable_debug_logging = true

# Define a custom schema for your spans
span_attributes = {
  "host.name": "_host.name",
  "task.name": "_task.name",
  "task.module": "_task.action",
  "task.path": "_task.path",
  "task.status": "_result.task_status",
  "result.changed": "_result.changed",
  "result.failed": "_result.failed",
  "result.rc": "_result.rc",
  "result.stdout": "_result.stdout"
}


# --- Environment Variables ---
# Configure the OTLP exporter. This will use the system's CA trust store by default.
export OTEL_EXPORTER_OTLP_ENDPOINT="https://my-collector.internal:4317"
export OTEL_SERVICE_NAME="my-ansible-automation"
"""

import os
import ssl
from functools import reduce
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
    CALLBACK_NAME = 'community.general.opentelemetry'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, display=None):
        super(CallbackModule, self).__init__(display=display)
        if not HAS_OTEL:
            raise AnsibleError(f'The opentelemetry libraries must be installed to use this plugin. Error: {OTEL_IMPORT_ERROR}')

        self.tracer = None
        self.tracer_provider = None
        self.playbook_span = None
        self.errors_in_playbook = 0
        self.debug_enabled = False
        self.custom_attributes = {}

    def _debug(self, msg):
        if self.debug_enabled:
            self._display.v(f"[opentelemetry callback] {msg}")

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(task_keys, var_options, direct)
        self.debug_enabled = self.get_option('enable_debug_logging')
        self.custom_attributes = self.get_option('span_attributes') or {}
        self._debug("Options loaded.")
        if self.custom_attributes:
            self._debug(f"Custom span attributes configured: {self.custom_attributes}")

    def _init_otel(self):
        if self.tracer:
            return
        self._debug("Initializing OpenTelemetry SDK...")
        resource = Resource.create({SERVICE_NAME: self.get_option('otel_service_name')})
        self.tracer_provider = TracerProvider(resource=resource)
        protocol = os.getenv('OTEL_EXPORTER_OTLP_TRACES_PROTOCOL', 'grpc')
        self._debug(f"Using OTLP protocol: {protocol}")

        exporter = None
        if protocol == 'grpc':
            self._debug("Configuring gRPC exporter to use system default CA trust store.")
            credentials = ssl.create_default_context()
            exporter = GRPCSpanExporter(credentials=credentials)
        elif protocol == 'http/protobuf':
            exporter = HTTPSpanExporter()

        if exporter:
            processor = BatchSpanProcessor(exporter)
            self.tracer_provider.add_span_processor(processor)
            trace.set_tracer_provider(self.tracer_provider)
            self.tracer = trace.get_tracer("ansible.opentelemetry.callback", ansible_version)
            self._debug("Tracer initialized successfully.")
        else:
            self._display.warning("No valid OTLP exporter configured. Traces will not be sent.")

    def v2_playbook_on_start(self, playbook):
        self._init_otel()
        if not self.tracer: return

        playbook_name = basename(playbook._file_name)
        self._debug(f"Starting trace for playbook: {playbook_name}")
        traceparent = self.get_option('traceparent')
        parent_context = TraceContextTextMapPropagator().extract({'traceparent': traceparent}) if traceparent else None

        self.playbook_span = self.tracer.start_span(
            name=f"playbook: {playbook_name}",
            kind=SpanKind.SERVER,
            context=parent_context,
        )
        self.playbook_span.set_attribute("ansible.playbook.name", playbook_name)
        self.playbook_span.set_attribute("ansible.version", ansible_version)

    def _get_path_from_obj(self, obj, path):
        """Safely gets a value from a nested object using a dot-separated path."""
        try:
            return reduce(getattr, path.split('.'), obj)
        except AttributeError:
            self._debug(f"Could not find path '{path}' in result object. Skipping attribute.")
            return None

    def _create_task_result_span(self, result, status_string: str):
        if not self.playbook_span: return

        task = result._task
        host = result._host
        span_name = f"{task.get_name()} on {host.get_name()}"
        self._debug(f"Creating span for result: {span_name}")

        parent_context = trace.set_span_in_context(self.playbook_span)
        span = self.tracer.start_span(name=span_name, context=parent_context)

        # Inject task status into the result object so it can be mapped by custom attributes
        result._result['task_status'] = status_string

        # Populate attributes based on the custom mapping in ansible.cfg
        for attr_name, attr_path in self.custom_attributes.items():
            value = self._get_path_from_obj(result, attr_path)
            if value is not None:
                # OTel attributes can't be complex types, so convert to string.
                span.set_attribute(attr_name, str(value))
        
        # Determine status code and record errors
        status_code = StatusCode.OK
        if status_string == "failed":
            self.errors_in_playbook += 1
            status_code = StatusCode.ERROR
            msg = result._result.get('msg', 'Task failed without a specific message.')
            span.record_exception(Exception(msg))

        span.set_status(Status(status_code))
        span.end()

    def v2_runner_on_ok(self, result):
        self._create_task_result_span(result, "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        status = "ignored" if ignore_errors else "failed"
        self._create_task_result_span(result, status)

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
        