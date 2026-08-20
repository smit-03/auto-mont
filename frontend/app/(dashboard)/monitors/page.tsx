"use client";

import { useMemo, useState } from "react";
import {
  Check,
  Copy,
  Eye,
  PlusCircle,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  useMonitors,
  useCreateMonitor,
  useDeleteMonitor,
  useUpdateMonitor,
  useIntegrations,
  useIntegrationWorkflows,
} from "@/lib/api";

// Every type the backend actually evaluates. The "schema" type was removed
// entirely: workflow-level schema *drift* detection already runs in the polling
// path against a stored fingerprint, so a schema monitor duplicated a shipped
// detector. Cron is evaluated by HeartbeatEngine._check_cron_schedule.
const MONITOR_TYPES = ["heartbeat", "outcome", "cron"];

// Monitor types that assert against a specific workflow's output rather than an
// inbound ping. The backend requires workflow_id for "outcome" and rejects the
// create without it.
const WORKFLOW_SCOPED_TYPES = ["outcome"];

const EMPTY_FORM = {
  name: "",
  monitor_type: "heartbeat",
  grace_period_s: 300,
  expected_cron: "",
  integration_id: "",
  workflow_id: "",
  min_records: "",
  field_exists: "",
  range_field: "",
  range_min: "",
  range_max: "",
};

/**
 * Assemble expected_outcome from the filled-in assertion inputs.
 *
 * Only non-empty assertions are included. An assertion sent with an empty value
 * would be evaluated as misconfigured by the backend and fire on every run.
 */
function buildExpectedOutcome(form: typeof EMPTY_FORM) {
  const outcome: Record<string, unknown> = {};

  if (form.min_records !== "") {
    outcome.min_records = Number(form.min_records);
  }
  if (form.field_exists.trim() !== "") {
    // Comma-separated input becomes the list form the backend accepts.
    const fields = form.field_exists
      .split(",")
      .map((f) => f.trim())
      .filter(Boolean);
    outcome.field_exists = fields.length === 1 ? fields[0] : fields;
  }
  if (form.range_field.trim() !== "" && (form.range_min !== "" || form.range_max !== "")) {
    const range: Record<string, unknown> = { field: form.range_field.trim() };
    if (form.range_min !== "") range.min = Number(form.range_min);
    if (form.range_max !== "") range.max = Number(form.range_max);
    outcome.value_range = range;
  }

  return Object.keys(outcome).length > 0 ? outcome : null;
}

export default function MonitorsPage() {
  const { data: monitors = [], isLoading } = useMonitors();
  const createMonitor = useCreateMonitor();
  const deleteMonitor = useDeleteMonitor();
  const updateMonitor = useUpdateMonitor();

  const [showForm, setShowForm] = useState(false);
  const [selectedMonitorId, setSelectedMonitorId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const { data: integrations = [] } = useIntegrations();
  const { data: workflows = [], isLoading: workflowsLoading } =
    useIntegrationWorkflows(form.integration_id || undefined);

  const isWorkflowScoped = WORKFLOW_SCOPED_TYPES.includes(form.monitor_type);
  const isPingDriven = !isWorkflowScoped;

  const selectedMonitor = useMemo(
    () => monitors.find((monitor) => monitor.id === selectedMonitorId) ?? null,
    [monitors, selectedMonitorId]
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (form.monitor_type === "cron" && !form.expected_cron.trim()) {
      // Same reasoning as the outcome check below: _check_cron_schedule returns
      // early without an expression, so the monitor would never fire.
      setError("A cron monitor needs a schedule expression.");
      return;
    }

    const expectedOutcome = buildExpectedOutcome(form);
    if (form.monitor_type === "outcome" && !expectedOutcome) {
      // Caught here rather than server-side: a monitor with no assertions can
      // never fire, so it would sit in this list looking configured while
      // watching nothing.
      setError("An outcome monitor needs at least one assertion.");
      return;
    }

    try {
      await createMonitor.mutateAsync({
        name: form.name,
        monitor_type: form.monitor_type,
        grace_period_s: form.grace_period_s,
        ...(form.integration_id ? { integration_id: form.integration_id } : {}),
        ...(isWorkflowScoped && form.workflow_id
          ? { workflow_id: form.workflow_id }
          : {}),
        ...(form.monitor_type === "cron" && form.expected_cron.trim()
          ? { expected_cron: form.expected_cron.trim() }
          : {}),
        ...(expectedOutcome ? { expected_outcome: expectedOutcome } : {}),
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleDelete = async (monitorId: string) => {
    if (!window.confirm("Delete this monitor?")) return;
    try {
      await deleteMonitor.mutateAsync(monitorId);
      if (selectedMonitorId === monitorId) {
        setSelectedMonitorId(null);
      }
    } catch {
      setError("Failed to delete monitor.");
    }
  };

  const handleToggleMute = async (monitorId: string, currentValue: boolean) => {
    try {
      await updateMonitor.mutateAsync({ id: monitorId, data: { alert_on_miss: !currentValue } });
    } catch {
      setError("Failed to update monitor.");
    }
  };

  const handleCopyPingUrl = async (monitorId: string, pingUrl?: string | null) => {
    if (!pingUrl) return;
    try {
      await navigator.clipboard.writeText(pingUrl);
      setCopiedId(monitorId);
      window.setTimeout(() => setCopiedId(null), 1500);
    } catch {
      setError("Failed to copy ping URL.");
    }
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Monitors</h1>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
        >
          <PlusCircle className="h-4 w-4" />
          {showForm ? "Cancel" : "Create Monitor"}
        </button>
      </div>

      {showForm && (
        <div className="mb-6 max-w-md rounded-lg bg-white p-6 shadow">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium">Name</label>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="mt-1 w-full rounded-md border p-2"
                placeholder="Nightly sync heartbeat"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">Type</label>
              <select
                value={form.monitor_type}
                onChange={(e) =>
                  setForm({ ...form, monitor_type: e.target.value })
                }
                className="mt-1 w-full rounded-md border p-2 capitalize"
              >
                {MONITOR_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            {form.monitor_type === "cron" && (
              <div>
                <label className="block text-sm font-medium">
                  Schedule (cron expression)
                </label>
                <input
                  value={form.expected_cron}
                  onChange={(e) =>
                    setForm({ ...form, expected_cron: e.target.value })
                  }
                  className="mt-1 w-full rounded-md border p-2 font-mono"
                  placeholder="0 9 * * *"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Standard 5-field cron, evaluated in UTC. The monitor alerts
                  when a scheduled run&apos;s ping does not arrive within the
                  grace period.
                </p>
              </div>
            )}
            {isPingDriven && (
              <div>
                <label className="block text-sm font-medium">
                  Grace Period (seconds)
                </label>
                <input
                  type="number"
                  min="30"
                  max="3600"
                  value={form.grace_period_s}
                  onChange={(e) =>
                    setForm({ ...form, grace_period_s: parseInt(e.target.value) })
                  }
                  className="mt-1 w-full rounded-md border p-2"
                />
              </div>
            )}

            {isWorkflowScoped && (
              <>
                <div>
                  <label className="block text-sm font-medium">Integration</label>
                  <select
                    required
                    value={form.integration_id}
                    onChange={(e) =>
                      // Workflows belong to an integration, so changing it
                      // invalidates any workflow already picked.
                      setForm({
                        ...form,
                        integration_id: e.target.value,
                        workflow_id: "",
                      })
                    }
                    className="mt-1 w-full rounded-md border p-2"
                  >
                    <option value="">Select an integration…</option>
                    {integrations.map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.display_name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium">Workflow</label>
                  <select
                    required
                    disabled={!form.integration_id || workflowsLoading}
                    value={form.workflow_id}
                    onChange={(e) =>
                      setForm({ ...form, workflow_id: e.target.value })
                    }
                    className="mt-1 w-full rounded-md border p-2 disabled:bg-gray-50"
                  >
                    <option value="">
                      {!form.integration_id
                        ? "Select an integration first"
                        : workflowsLoading
                          ? "Loading workflows…"
                          : "Select a workflow…"}
                    </option>
                    {workflows.map((w) => (
                      <option key={w.workflow_id} value={w.workflow_id}>
                        {w.workflow_name || w.workflow_id}
                      </option>
                    ))}
                  </select>
                  {form.integration_id &&
                    !workflowsLoading &&
                    workflows.length === 0 && (
                      <p className="mt-1 text-xs text-gray-500">
                        No workflows seen yet on this integration. A workflow
                        appears here once it has run at least once.
                      </p>
                    )}
                </div>
              </>
            )}

            {form.monitor_type === "outcome" && (
              <div className="space-y-3 rounded-md border border-dashed p-3">
                <p className="text-xs uppercase tracking-wide text-gray-500">
                  Assertions — all must pass
                </p>

                <div>
                  <label className="block text-sm font-medium">
                    Minimum records
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={form.min_records}
                    onChange={(e) =>
                      setForm({ ...form, min_records: e.target.value })
                    }
                    className="mt-1 w-full rounded-md border p-2"
                    placeholder="e.g. 1 — alert if the run writes nothing"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium">
                    Required fields
                  </label>
                  <input
                    type="text"
                    value={form.field_exists}
                    onChange={(e) =>
                      setForm({ ...form, field_exists: e.target.value })
                    }
                    className="mt-1 w-full rounded-md border p-2"
                    placeholder="order_id, customer.email"
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    Comma-separated. Dotted paths reach nested fields.
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-medium">
                    Value range
                  </label>
                  <div className="mt-1 grid grid-cols-3 gap-2">
                    <input
                      type="text"
                      value={form.range_field}
                      onChange={(e) =>
                        setForm({ ...form, range_field: e.target.value })
                      }
                      className="rounded-md border p-2"
                      placeholder="field"
                    />
                    <input
                      type="number"
                      value={form.range_min}
                      onChange={(e) =>
                        setForm({ ...form, range_min: e.target.value })
                      }
                      className="rounded-md border p-2"
                      placeholder="min"
                    />
                    <input
                      type="number"
                      value={form.range_max}
                      onChange={(e) =>
                        setForm({ ...form, range_max: e.target.value })
                      }
                      className="rounded-md border p-2"
                      placeholder="max"
                    />
                  </div>
                  <p className="mt-1 text-xs text-gray-500">
                    Either bound may be left blank for a one-sided check.
                  </p>
                </div>
              </div>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={createMonitor.isPending}
              className="w-full rounded-lg bg-blue-600 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {createMonitor.isPending ? "Creating…" : "Create Monitor"}
            </button>
          </form>
        </div>
      )}

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading monitors…</p>
        </div>
      ) : monitors.length === 0 ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">
            No monitors configured yet. Create a heartbeat monitor to catch a
            workflow that stops running, or an outcome monitor to catch one that
            runs but produces the wrong output.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {monitors.map((monitor) => (
            <div key={monitor.id} className="rounded-lg bg-white p-4 shadow">
              <div className="flex items-start justify-between gap-4">
                <button
                  type="button"
                  onClick={() => setSelectedMonitorId(monitor.id)}
                  className="flex-1 text-left"
                >
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold">{monitor.name}</h3>
                    <Eye className="h-4 w-4 text-gray-400" />
                  </div>
                  <p className="text-sm text-gray-500">
                    <span className="capitalize">{monitor.monitor_type}</span>
                    {monitor.workflow_id && (
                      <span className="font-mono text-xs">
                        {" · "}
                        {monitor.workflow_id}
                      </span>
                    )}
                  </p>
                </button>
                <div className="flex items-center gap-2">
                  <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize">
                    {monitor.last_status}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleDelete(monitor.id)}
                    disabled={deleteMonitor.isPending}
                    className="rounded-md p-2 text-gray-400 hover:bg-gray-100 hover:text-red-600 disabled:opacity-50"
                    aria-label="Delete monitor"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              {monitor.last_ping_at && (
                <p className="mt-2 text-xs text-gray-500">
                  Last ping {new Date(monitor.last_ping_at).toLocaleString()}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {selectedMonitor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold">{selectedMonitor.name}</h2>
                <p className="text-sm text-gray-500 capitalize">{selectedMonitor.monitor_type}</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedMonitorId(null)}
                className="rounded-md p-2 text-gray-400 hover:bg-gray-100"
                aria-label="Close details"
              >
                <XCircle className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-md bg-gray-50 p-3">
                  <p className="text-xs uppercase tracking-wide text-gray-500">Status</p>
                  <p className="mt-1 font-medium capitalize">{selectedMonitor.last_status}</p>
                </div>
                <div className="rounded-md bg-gray-50 p-3">
                  <p className="text-xs uppercase tracking-wide text-gray-500">Grace period</p>
                  <p className="mt-1 font-medium">{selectedMonitor.grace_period_s}s</p>
                </div>
              </div>

              <div className="rounded-md border p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-gray-500">Ping URL</p>
                    <p className="mt-1 break-all font-mono text-xs text-gray-700">
                      {selectedMonitor.ping_url || "Not available for this monitor type"}
                    </p>
                  </div>
                  {selectedMonitor.ping_url && (
                    <button
                      type="button"
                      onClick={() => handleCopyPingUrl(selectedMonitor.id, selectedMonitor.ping_url)}
                      className="rounded-md border p-2 text-gray-600 hover:bg-gray-50"
                      aria-label="Copy ping URL"
                    >
                      {copiedId === selectedMonitor.id ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </button>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <p className="text-xs uppercase tracking-wide text-gray-500">Alert on miss</p>
                  <p className="mt-1 text-sm">
                    {selectedMonitor.alert_on_miss ? "Alerts enabled" : "Alerts muted"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleToggleMute(selectedMonitor.id, selectedMonitor.alert_on_miss)}
                  disabled={updateMonitor.isPending}
                  className={`rounded-md px-3 py-2 text-sm font-medium ${
                    selectedMonitor.alert_on_miss
                      ? "bg-gray-100 text-gray-700"
                      : "bg-blue-600 text-white"
                  } disabled:opacity-50`}
                >
                  {selectedMonitor.alert_on_miss ? "Mute" : "Unmute"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
