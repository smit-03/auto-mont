"use client";

import { useState } from "react";
import { CreditCard, PlusCircle, Trash2 } from "lucide-react";
import {
  useCredentials,
  useCreateCredential,
  useDeleteCredential,
} from "@/lib/api";

const PROVIDERS = ["google", "microsoft", "github", "slack", "custom"];

export default function CredentialsPage() {
  const { data: credentials = [], isLoading } = useCredentials();
  const createCredential = useCreateCredential();
  const deleteCredential = useDeleteCredential();

  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    display_name: "",
    provider: "google",
    token: "",
    refresh_token: "",
    expires_at: "",
  });

  const resetForm = () =>
    setForm({
      display_name: "",
      provider: "google",
      token: "",
      refresh_token: "",
      expires_at: "",
    });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await createCredential.mutateAsync({
        display_name: form.display_name,
        provider: form.provider,
        token: form.token,
        refresh_token: form.refresh_token || undefined,
        expires_at: form.expires_at
          ? new Date(form.expires_at).toISOString()
          : undefined,
      });
      // Clear secrets from memory immediately; never echo them back.
      resetForm();
      setShowForm(false);
    } catch {
      // Never surface token values in the error path.
      setError("Failed to save credential. Check the values and try again.");
    }
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Credentials</h1>
          <p className="text-gray-600">
            Track OAuth token expiry for your workflows
          </p>
        </div>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
        >
          <PlusCircle className="h-4 w-4" />
          {showForm ? "Cancel" : "Track Credential"}
        </button>
      </div>

      {showForm && (
        <div className="mb-6 max-w-md rounded-lg bg-white p-6 shadow">
          <form onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
            <div>
              <label className="block text-sm font-medium">Display Name</label>
              <input
                type="text"
                required
                value={form.display_name}
                onChange={(e) =>
                  setForm({ ...form, display_name: e.target.value })
                }
                className="mt-1 w-full rounded-md border p-2"
                placeholder="Google Sheets OAuth"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">Provider</label>
              <select
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
                className="mt-1 w-full rounded-md border p-2 capitalize"
              >
                {PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium">Access Token</label>
              <input
                type="password"
                required
                autoComplete="new-password"
                value={form.token}
                onChange={(e) => setForm({ ...form, token: e.target.value })}
                className="mt-1 w-full rounded-md border p-2"
                placeholder="Stored encrypted; never shown again"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">
                Refresh Token (optional)
              </label>
              <input
                type="password"
                autoComplete="new-password"
                value={form.refresh_token}
                onChange={(e) =>
                  setForm({ ...form, refresh_token: e.target.value })
                }
                className="mt-1 w-full rounded-md border p-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium">
                Expires At (optional)
              </label>
              <input
                type="datetime-local"
                value={form.expires_at}
                onChange={(e) =>
                  setForm({ ...form, expires_at: e.target.value })
                }
                className="mt-1 w-full rounded-md border p-2"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={createCredential.isPending}
              className="w-full rounded-lg bg-blue-600 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {createCredential.isPending ? "Saving…" : "Track Credential"}
            </button>
          </form>
        </div>
      )}

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading credentials…</p>
        </div>
      ) : credentials.length === 0 ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <div className="flex items-center gap-3">
            <CreditCard className="h-5 w-5 text-gray-400" />
            <div>
              <p className="text-sm font-medium">No credentials tracked</p>
              <p className="text-xs text-gray-500">
                Track a credential to monitor its OAuth token expiry
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {credentials.map((cred) => (
            <div key={cred.id} className="rounded-lg bg-white p-4 shadow">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">{cred.display_name}</h3>
                  <p className="text-sm text-gray-500 capitalize">
                    {cred.provider}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <span
                      className={`rounded-full px-2 py-1 text-xs font-medium ${
                        cred.is_expiring_soon
                          ? "bg-yellow-50 text-yellow-700"
                          : "bg-gray-100 text-gray-700"
                      }`}
                    >
                      {cred.status}
                    </span>
                    {cred.expires_at && (
                      <p className="mt-1 text-xs text-gray-500">
                        Expires {new Date(cred.expires_at).toLocaleString()}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => deleteCredential.mutate(cred.id)}
                    disabled={deleteCredential.isPending}
                    className="rounded-md p-2 text-gray-400 hover:bg-gray-100 hover:text-red-600 disabled:opacity-50"
                    aria-label="Delete credential"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
