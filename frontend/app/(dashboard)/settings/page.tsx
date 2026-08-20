"use client";

import { useState } from "react";
import {
  AlertCircle,
  Check,
  Mail,
  PlusCircle,
  Send,
  MessageSquare,
  Trash2,
} from "lucide-react";
import {
  useChannels,
  useCreateChannel,
  useDeleteChannel,
  useTestChannel,
  useUpdateChannel,
  type ChannelRead,
} from "@/lib/api";

const SEVERITIES = ["info", "warning", "critical"];

const EMPTY_FORM = {
  channel_type: "slack",
  display_name: "",
  destination: "",
  min_severity: "warning",
};

// Label, placeholder and help text all switch on type. The two destinations are
// not interchangeable and the backend rejects a mismatched pair, so the form
// states which one it wants rather than letting the user discover it on submit.
const DESTINATION_COPY: Record<
  string,
  { label: string; placeholder: string; help: string }
> = {
  slack: {
    label: "Incoming webhook URL",
    placeholder: "https://hooks.slack.com/services/T000/B000/XXXX",
    help: "Slack > your app > Incoming Webhooks. Must be an https:// slack.com URL.",
  },
  email: {
    label: "Email address",
    placeholder: "alerts@yourcompany.com",
    help: "One address per channel. Add a second channel to route a different severity elsewhere.",
  },
};

export default function SettingsPage() {
  const { data: channels, isLoading } = useChannels();
  const createChannel = useCreateChannel();
  const updateChannel = useUpdateChannel();
  const deleteChannel = useDeleteChannel();
  const testChannel = useTestChannel();

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  // Test results are per-row and transient: they describe one delivery attempt,
  // not channel state, so they are neither persisted nor refetched.
  const [testResults, setTestResults] = useState<
    Record<string, { delivered: boolean; error?: string | null }>
  >({});

  const copy = DESTINATION_COPY[form.channel_type];

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      await createChannel.mutateAsync({
        channel_type: form.channel_type,
        display_name: form.display_name.trim(),
        destination: form.destination.trim(),
        min_severity: form.min_severity,
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to add channel");
    }
  }

  async function handleTest(channel: ChannelRead) {
    try {
      const result = await testChannel.mutateAsync(channel.id);
      setTestResults((prev) => ({
        ...prev,
        [channel.id]: { delivered: result.delivered, error: result.error_message },
      }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [channel.id]: {
          delivered: false,
          error: err instanceof Error ? err.message : "Test failed",
        },
      }));
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-600">Workspace configuration.</p>
      </div>

      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Notification channels
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-gray-600">
              Where this workspace&apos;s alerts are delivered. A workspace with no
              enabled channel receives no alerts at all: detection still runs and
              alerts are still recorded, but nothing is sent anywhere.
            </p>
          </div>
          <button
            onClick={() => setShowForm((s) => !s)}
            className="flex shrink-0 items-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
          >
            <PlusCircle className="h-4 w-4" />
            Add channel
          </button>
        </div>

        {showForm && (
          <form
            onSubmit={handleCreate}
            className="space-y-4 rounded-lg border bg-white p-6 shadow-sm"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Type</span>
                <select
                  value={form.channel_type}
                  onChange={(e) =>
                    // Clear the destination on type change: a webhook URL left in
                    // the field after switching to email is certain to be
                    // rejected, and the error would point at the wrong thing.
                    setForm({
                      ...form,
                      channel_type: e.target.value,
                      destination: "",
                    })
                  }
                  className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
                >
                  <option value="slack">Slack</option>
                  <option value="email">Email</option>
                </select>
              </label>

              <label className="block">
                <span className="text-sm font-medium text-gray-700">Name</span>
                <input
                  required
                  value={form.display_name}
                  onChange={(e) =>
                    setForm({ ...form, display_name: e.target.value })
                  }
                  placeholder="Engineering alerts"
                  className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
                />
              </label>
            </div>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                {copy.label}
              </span>
              <input
                required
                value={form.destination}
                onChange={(e) =>
                  setForm({ ...form, destination: e.target.value })
                }
                placeholder={copy.placeholder}
                className="mt-1 w-full rounded-lg border px-3 py-2 font-mono text-sm"
              />
              <span className="mt-1 block text-xs text-gray-500">{copy.help}</span>
            </label>

            <label className="block sm:w-64">
              <span className="text-sm font-medium text-gray-700">
                Minimum severity
              </span>
              <select
                value={form.min_severity}
                onChange={(e) =>
                  setForm({ ...form, min_severity: e.target.value })
                }
                className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
              >
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs text-gray-500">
                Alerts below this severity are not delivered here.
              </span>
            </label>

            {formError && (
              <p className="flex items-start gap-2 text-sm text-red-600">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                {formError}
              </p>
            )}

            <div className="flex gap-2">
              <button
                type="submit"
                disabled={createChannel.isPending}
                className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
              >
                {createChannel.isPending ? "Adding..." : "Add channel"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowForm(false);
                  setFormError(null);
                }}
                className="rounded-lg border px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {isLoading ? (
          <p className="text-sm text-gray-500">Loading channels...</p>
        ) : !channels?.length ? (
          <div className="rounded-lg border border-dashed bg-white p-8 text-center">
            <p className="text-sm font-medium text-gray-900">
              No notification channels
            </p>
            <p className="mt-1 text-sm text-gray-600">
              Alerts are being recorded but not delivered. Add a Slack webhook or
              an email address to start receiving them.
            </p>
          </div>
        ) : (
          <ul className="divide-y rounded-lg border bg-white shadow-sm">
            {channels.map((channel) => {
              const result = testResults[channel.id];
              return (
                <li
                  key={channel.id}
                  className="flex flex-wrap items-center gap-4 p-4"
                >
                  <div className="shrink-0 text-gray-400">
                    {channel.channel_type === "email" ? (
                      <Mail className="h-5 w-5" />
                    ) : (
                      <MessageSquare className="h-5 w-5" />
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-gray-900">
                      {channel.display_name}
                    </p>
                    <p className="truncate font-mono text-xs text-gray-500">
                      {channel.destination_hint ?? "hidden"}
                    </p>
                    {result && (
                      <p
                        className={`mt-1 flex items-center gap-1 text-xs ${
                          result.delivered ? "text-green-600" : "text-red-600"
                        }`}
                      >
                        {result.delivered ? (
                          <>
                            <Check className="h-3 w-3" /> Test delivered
                          </>
                        ) : (
                          <>
                            <AlertCircle className="h-3 w-3" />
                            {result.error || "Test failed"}
                          </>
                        )}
                      </p>
                    )}
                  </div>

                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                    min {channel.min_severity}
                  </span>

                  <label className="flex items-center gap-2 text-xs text-gray-600">
                    <input
                      type="checkbox"
                      checked={channel.enabled}
                      onChange={(e) =>
                        updateChannel.mutate({
                          id: channel.id,
                          data: { enabled: e.target.checked },
                        })
                      }
                    />
                    Enabled
                  </label>

                  <button
                    onClick={() => handleTest(channel)}
                    disabled={testChannel.isPending}
                    title="Send a test notification"
                    className="flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    <Send className="h-3 w-3" />
                    Test
                  </button>

                  <button
                    onClick={() => deleteChannel.mutate(channel.id)}
                    title="Delete channel"
                    className="rounded-lg border border-red-200 p-1.5 text-red-600 hover:bg-red-50"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
