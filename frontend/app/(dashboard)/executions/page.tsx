"use client";

import { Activity } from "lucide-react";

export default function ExecutionsPage() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Executions</h1>

      <div className="rounded-lg bg-white p-6 shadow">
        <div className="flex items-center gap-3">
          <Activity className="h-5 w-5 text-gray-400" />
          <div>
            <p className="text-sm font-medium">Execution history not available yet</p>
            <p className="text-xs text-gray-500">
              The executions API is not implemented in the current backend.
              Polled runs are ingested and diagnosed, but the read endpoint is
              still pending — this view will populate once it ships.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
