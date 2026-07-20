"use client";

import { PlusCircle } from "lucide-react";

export default function MonitorsPage() {
  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Monitors</h1>
        <button className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">
          <PlusCircle className="h-4 w-4" />
          Create Monitor
        </button>
      </div>

      <div className="rounded-lg bg-white p-6 shadow">
        <p className="text-gray-600">
          No heartbeat monitors configured yet. Create one to catch silent
          workflow failures.
        </p>
      </div>
    </div>
  );
}