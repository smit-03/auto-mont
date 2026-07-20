"use client";

import { PlusCircle } from "lucide-react";
import Link from "next/link";

export default function IntegrationsPage() {
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
    </div>
  );
}