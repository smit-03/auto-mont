"use client";

import { PlusCircle } from "lucide-react";
import Link from "next/link";
import { useIntegrations } from "@/lib/api";

export default function IntegrationsPage() {
  const { data: integrations = [], isLoading } = useIntegrations();

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Integrations</h1>
        <Link
          href="/integrations/new"
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
        >
          <PlusCircle className="h-4 w-4" />
          Connect n8n
        </Link>
      </div>

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading integrations…</p>
        </div>
      ) : integrations.length === 0 ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">
            No integrations connected yet.{" "}
            <Link
              href="/integrations/new"
              className="text-blue-600 hover:underline"
            >
              Connect your first n8n instance
            </Link>
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {integrations.map((integration) => (
            <div
              key={integration.id}
              className="rounded-lg bg-white p-4 shadow"
            >
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">{integration.display_name}</h3>
                  <p className="text-sm text-gray-500 capitalize">
                    {integration.platform}
                  </p>
                </div>
                <div className="text-right">
                  <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize">
                    {integration.status}
                  </span>
                  {integration.last_polled_at && (
                    <p className="mt-1 text-xs text-gray-500">
                      Last polled{" "}
                      {new Date(integration.last_polled_at).toLocaleString()}
                    </p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
