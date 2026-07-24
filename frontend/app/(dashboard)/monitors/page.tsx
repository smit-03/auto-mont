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
import { useMonitors, useCreateMonitor, useDeleteMonitor, useUpdateMonitor } from "@/lib/api";

const MONITOR_TYPES = ["heartbeat", "outcome", "cron", "schema"];

export default function MonitorsPage() {
  const { data: monitors = [], isLoading } = useMonitors();
  const createMonitor = useCreateMonitor();
  const deleteMonitor = useDeleteMonitor();
  const updateMonitor = useUpdateMonitor();

  const [showForm, setShowForm] = useState(false);
  const [selectedMonitorId, setSelectedMonitorId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    monitor_type: "heartbeat",
    grace_period_s: 300,
  });

  const selectedMonitor = useMemo(
    () => monitors.find((monitor) => monitor.id === selectedMonitorId) ?? null,
    [monitors, selectedMonitorId]
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await createMonitor.mutateAsync(form);
      setForm({ name: "", monitor_type: "heartbeat", grace_period_s: 300 });
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
            No heartbeat monitors configured yet. Create one to catch silent
            workflow failures.
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
                  <p className="text-sm text-gray-500 capitalize">
                    {monitor.monitor_type}
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
