"use client";

import { useState } from "react";
import { ChevronRight, Filter } from "lucide-react";
import Link from "next/link";
import { useAlerts } from "@/lib/api";

export default function AlertsPage() {
  const [severity, setSeverity] = useState<string>("all");
  const { data: alerts = [] } = useAlerts();

  const filteredAlerts = alerts.filter(
    (a) => severity === "all" || a.severity === severity
  );

  const getSeverityColor = (s: string) => {
    switch (s) {
      case "critical":
        return "text-red-600 bg-red-50";
      case "warning":
        return "text-yellow-600 bg-yellow-50";
      default:
        return "text-blue-600 bg-blue-50";
    }
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Alerts</h1>

        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-gray-500" />
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            className="rounded-md border p-1 text-sm"
          >
            <option value="all">All Severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>
      </div>

      {filteredAlerts.length === 0 ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">No alerts to display.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredAlerts.map((alert) => (
            <Link
              key={alert.id}
              href={`/alerts/${alert.id}`}
              className={`block rounded-lg p-4 shadow transition hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${getSeverityColor(alert.severity)}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h3 className="font-semibold">{alert.title}</h3>
                  <p className="mt-1 text-sm">{alert.description}</p>
                  <p className="mt-2 text-xs text-gray-500">
                    {new Date(alert.created_at).toLocaleString()}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="rounded-full px-2 py-1 text-xs font-medium capitalize">
                    {alert.severity}
                  </span>
                  <ChevronRight className="h-4 w-4 opacity-60" />
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}