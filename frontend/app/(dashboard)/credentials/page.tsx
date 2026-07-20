"use client";

import { CreditCard } from "lucide-react";

export default function CredentialsPage() {
  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Credentials</h1>
        <p className="text-gray-600">Track OAuth token expiry for your workflows</p>
      </div>

      <div className="rounded-lg bg-white p-6 shadow">
        <div className="flex items-center gap-3">
          <CreditCard className="h-5 w-5 text-gray-400" />
          <div>
            <p className="text-sm font-medium">No credentials tracked</p>
            <p className="text-xs text-gray-500">
              Credentials are automatically tracked when you connect integrations
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}