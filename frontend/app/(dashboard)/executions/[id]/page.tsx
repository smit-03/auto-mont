"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import {
  useExecution,
  useExecutionDiagnostic,
  type ExecutionDetail,
} from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  success: "text-green-700 bg-green-50 ring-green-200",
  error: "text-red-700 bg-red-50 ring-red-200",
  silent_fail: "text-yellow-800 bg-yellow-50 ring-yellow-200",
  timeout: "text-orange-700 bg-orange-50 ring-orange-200",
  running: "text-blue-700 bg-blue-50 ring-blue-200",
};

type NodeRun = {
  name: string;
  status: string;
  durationMs: number | null;
  startTime: number | null;
  error: string | null;
};

/**
 * Pull the per-node run table out of an n8n raw_payload.
 *
 * The payload is the platform's untouched response, so every step is guarded:
 * a shape that does not match yields no rows rather than throwing and blanking
 * the page. Paths verified against stored payloads — runs live at
 * data.resultData.runData keyed by node name, each with executionStatus,
 * executionTime, startTime, and an error object when that node failed.
 */
function extractNodeRuns(payload: Record<string, unknown> | undefined): NodeRun[] {
  const resultData = (payload?.data as Record<string, unknown> | undefined)
    ?.resultData as Record<string, unknown> | undefined;
  const runData = resultData?.runData as
    | Record<string, Array<Record<string, unknown>>>
    | undefined;
  if (!runData || typeof runData !== "object") return [];

  const rows: NodeRun[] = [];
  for (const [name, runs] of Object.entries(runData)) {
    if (!Array.isArray(runs)) continue;
    for (const run of runs) {
      const error = run?.error as Record<string, unknown> | undefined;
      rows.push({
        name,
        status: typeof run?.executionStatus === "string" ? run.executionStatus : "unknown",
        durationMs:
          typeof run?.executionTime === "number" ? run.executionTime : null,
        startTime: typeof run?.startTime === "number" ? run.startTime : null,
        error: typeof error?.message === "string" ? error.message : null,
      });
    }
  }

  // n8n returns runData as an object, and object key order is insertion order,
  // not execution order. Sorting by startTime puts the trace back in the order
  // the workflow actually ran.
  return rows.sort((a, b) => (a.startTime ?? 0) - (b.startTime ?? 0));
}

function Field({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
        {label}
      </dt>
      <dd className="mt-1 break-all font-mono text-sm text-gray-900">
        {value ?? <span className="font-sans italic text-gray-400">none</span>}
      </dd>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-white p-5 shadow">
      <h2 className="mb-3 text-sm font-semibold text-gray-900">{title}</h2>
      {children}
    </div>
  );
}

function NodeTrace({ execution }: { execution: ExecutionDetail }) {
  const nodes = extractNodeRuns(execution.raw_payload);

  if (nodes.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        This platform&apos;s payload carries no per-node trace.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
            <th className="pb-2 pr-4 font-medium">Node</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 font-medium">Duration</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node, i) => (
            <tr key={`${node.name}-${i}`} className="border-b last:border-0">
              <td className="py-2 pr-4 align-top">
                <span className="font-medium text-gray-900">{node.name}</span>
                {node.error && (
                  <p className="mt-1 font-mono text-xs text-red-600">
                    {node.error}
                  </p>
                )}
              </td>
              <td className="py-2 pr-4 align-top">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    node.status === "error"
                      ? "bg-red-50 text-red-700"
                      : node.status === "success"
                        ? "bg-green-50 text-green-700"
                        : "bg-gray-100 text-gray-700"
                  }`}
                >
                  {node.status}
                </span>
              </td>
              <td className="py-2 align-top text-gray-600">
                {node.durationMs == null ? "—" : `${node.durationMs}ms`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Diagnosis({ executionId }: { executionId: string }) {
  const { data, isLoading, error } = useExecutionDiagnostic(executionId);

  if (isLoading) {
    return <p className="text-sm text-gray-500">Diagnosing…</p>;
  }
  // The endpoint answers 404 when the rule engine finds nothing wrong. That is
  // an answer, not a failure, so it reads as an all-clear rather than an error.
  if (error || !data) {
    return (
      <p className="text-sm text-gray-500">
        No diagnosis — the rule engine found nothing wrong with this run.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize text-gray-700">
          {data.category}
        </span>
        <span className="text-xs text-gray-500">
          {Math.round(data.confidence * 100)}% confidence
          {data.requires_hitl && " · needs review"}
        </span>
      </div>
      <p className="text-sm font-medium text-gray-900">{data.root_cause}</p>
      <p className="text-sm text-gray-700">{data.suggested_fix}</p>
    </div>
  );
}

export default function ExecutionDetailPage({
  params,
}: {
  // Next 16 passes params as a promise; `use()` unwraps it in a client component.
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: execution, isLoading, error } = useExecution(id);

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href="/executions"
        className="mb-4 inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to executions
      </Link>

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading execution…</p>
        </div>
      ) : error ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-red-600">
            Could not load this execution: {(error as Error).message}
          </p>
        </div>
      ) : !execution ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Execution not found.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div
            className={`rounded-lg p-5 shadow ring-1 ${
              STATUS_STYLES[execution.status] ?? "text-gray-700 bg-white ring-gray-200"
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <h1 className="text-xl font-bold">
                {execution.workflow_name || execution.workflow_id}
              </h1>
              <span className="shrink-0 rounded-full bg-white/70 px-2 py-1 text-xs font-medium">
                {execution.status.replace("_", " ")}
              </span>
            </div>
            {execution.error_message && (
              <p className="mt-2 font-mono text-sm">
                {execution.error_node ? `${execution.error_node}: ` : ""}
                {execution.error_message}
              </p>
            )}
            <p className="mt-3 text-xs opacity-75">
              Ingested {new Date(execution.created_at).toLocaleString()}
            </p>
          </div>

          <Section title="Diagnosis">
            <Diagnosis executionId={execution.id} />
          </Section>

          <Section title="Node trace">
            <NodeTrace execution={execution} />
          </Section>

          <Section title="Run">
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="Started"
                value={
                  execution.started_at
                    ? new Date(execution.started_at).toLocaleString()
                    : null
                }
              />
              <Field
                label="Finished"
                value={
                  execution.finished_at
                    ? new Date(execution.finished_at).toLocaleString()
                    : null
                }
              />
              <Field
                label="Duration"
                value={
                  execution.duration_ms == null
                    ? null
                    : `${execution.duration_ms}ms`
                }
              />
              <Field label="Items processed" value={execution.items_processed} />
              <Field label="Nodes" value={execution.node_count} />
              <Field label="Platform" value={execution.platform} />
              <Field label="Workflow ID" value={execution.workflow_id} />
              <Field label="Platform run ID" value={execution.platform_run_id} />
            </dl>
          </Section>
        </div>
      )}
    </div>
  );
}
