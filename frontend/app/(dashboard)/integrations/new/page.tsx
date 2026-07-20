"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useCreateIntegration } from "@/lib/api";

export default function NewIntegrationPage() {
  const router = useRouter();
  const createIntegration = useCreateIntegration();
  const [formData, setFormData] = useState({
    platform: "n8n",
    display_name: "",
    api_key: "",
    base_url: "http://localhost:5678",
    poll_interval_s: 60,
  });
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    try {
      await createIntegration.mutateAsync(formData);
      router.push("/integrations");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Connect n8n</h1>

      <div className="max-w-md rounded-lg bg-white p-6 shadow">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium">Display Name</label>
            <input
              type="text"
              required
              value={formData.display_name}
              onChange={(e) =>
                setFormData({ ...formData, display_name: e.target.value })
              }
              className="mt-1 w-full rounded-md border p-2"
              placeholder="My Production n8n"
            />
          </div>

          <div>
            <label className="block text-sm font-medium">n8n URL</label>
            <input
              type="url"
              value={formData.base_url}
              onChange={(e) =>
                setFormData({ ...formData, base_url: e.target.value })
              }
              className="mt-1 w-full rounded-md border p-2"
              placeholder="https://your-instance.app.n8n.cloud"
            />
          </div>

          <div>
            <label className="block text-sm font-medium">API Key</label>
            <input
              type="password"
              required
              value={formData.api_key}
              onChange={(e) =>
                setFormData({ ...formData, api_key: e.target.value })
              }
              className="mt-1 w-full rounded-md border p-2"
              placeholder="n8n API key from Settings → API"
            />
          </div>

          <div>
            <label className="block text-sm font-medium">Poll Interval (seconds)</label>
            <input
              type="number"
              min="30"
              max="3600"
              value={formData.poll_interval_s}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  poll_interval_s: parseInt(e.target.value),
                })
              }
              className="mt-1 w-full rounded-md border p-2"
            />
          </div>

          {error && <p className="text-red-600 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={createIntegration.isPending}
            className="w-full rounded-lg bg-blue-600 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {createIntegration.isPending ? "Connecting..." : "Connect Integration"}
          </button>
        </form>
      </div>
    </div>
  );
}