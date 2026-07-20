"use client";

import { Activity, AlertTriangle, Shield } from "lucide-react";
import { useIntegrations, useAlerts, useMonitors } from "@/lib/api";

export default function DashboardPage() {
  const { data: integrations = [] } = useIntegrations();
  const { data: alerts = [] } = useAlerts();
  const { data: monitors = [] } = useMonitors();

  const activeIntegrations = integrations.filter((i) => i.status === "active").length;
  const criticalAlerts = alerts.filter(
    (a) => a.severity === "critical" && !a.resolved_at
  ).length;
  const healthyMonitors = monitors.filter(
    (m) => m.last_status === "healthy"
  ).length;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Dashboard</h1>

      <div className="grid gap-6 md:grid-cols-3">
        <div className="rounded-lg bg-white p-6 shadow">
          <div className="flex items-center gap-4">
            <Shield className="h-8 w-8 text-blue-600" />
            <div>
              <p className="text-sm text-gray-600">Active Integrations</p>
              <p className="text-3xl font-bold">{activeIntegrations}</p>
            </div>
          </div>
        </div>

        <div className="rounded-lg bg-white p-6 shadow">
          <div className="flex items-center gap-4">
            <AlertTriangle className="h-8 w-8 text-red-600" />
            <div>
              <p className="text-sm text-gray-600">Critical Alerts</p>
              <p className="text-3xl font-bold">{criticalAlerts}</p>
            </div>
          </div>
        </div>

        <div className="rounded-lg bg-white p-6 shadow">
          <div className="flex items-center gap-4">
            <Activity className="h-8 w-8 text-green-600" />
            <div>
              <p className="text-sm text-gray-600">Healthy Monitors</p>
              <p className="text-3xl font-bold">{healthyMonitors}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}