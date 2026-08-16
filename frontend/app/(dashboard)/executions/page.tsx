"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronRight, Filter } from "lucide-react";
import { useExecutions, type ExecutionRead } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  success: "text-green-700 bg-green-50",
  error: "text-red-700 bg-red-50",
  silent_fail: "text-yellow-800 bg-yellow-50",
  timeout: "text-orange-700 bg-orange-50",
  running: "text-blue-700 bg-blue-50",
};

function formatDuration(ms?: number | null) {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function ExecutionRow({ execution }: { execution: ExecutionRead }) {
  return (
    <Link
      href={`/executions/${execution.id}`}
      className="block rounded-lg bg-white p-4 shadow transition hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-gray-900">
            {execution.workflow_name || execution.workflow_id}
          </h3>
          {execution.error_message && (
            <p className="mt-1 truncate text-sm text-red-600">
              {execution.error_node ? `${execution.error_node}: ` : ""}
              {execution.error_message}
            </p>
          )}
          <p className="mt-2 text-xs text-gray-500">
            {new Date(execution.created_at).toLocaleString()} ·{" "}
            {formatDuration(execution.duration_ms)}
            {execution.items_processed != null &&
              ` · ${execution.items_processed} items`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`rounded-full px-2 py-1 text-xs font-medium ${
              STATUS_STYLES[execution.status] ?? "text-gray-700 bg-gray-100"
            }`}
          >
            {execution.status.replace("_", " ")}
          </span>
          <ChevronRight className="h-4 w-4 text-gray-400" />
        </div>
      </div>
    </Link>
  );
}

export default function ExecutionsPage() {
  const [status, setStatus] = useState<string>("all");
  const {
    data,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useExecutions({ status });

  // Filtering happens server-side so a filtered view pages through matching
  // rows rather than through whatever the first page happened to contain.
  const executions = data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Executions</h1>

        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-gray-500" />
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border p-1 text-sm"
          >
            <option value="all">All Statuses</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
            <option value="silent_fail">Silent Fail</option>
            <option value="timeout">Timeout</option>
            <option value="running">Running</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading executions…</p>
        </div>
      ) : error ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-red-600">
            Could not load executions: {(error as Error).message}
          </p>
        </div>
      ) : executions.length === 0 ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">
            No executions to display. Runs appear here once an integration has
            been polled.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {executions.map((execution) => (
            <ExecutionRow key={execution.id} execution={execution} />
          ))}

          {hasNextPage && (
            <button
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
              className="w-full rounded-lg bg-white p-3 text-sm font-medium text-gray-700 shadow transition hover:shadow-md disabled:opacity-60"
            >
              {isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
