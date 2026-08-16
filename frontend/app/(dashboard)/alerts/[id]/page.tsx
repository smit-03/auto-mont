"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useAlert } from "@/lib/api";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "text-red-700 bg-red-50 ring-red-200",
  warning: "text-yellow-700 bg-yellow-50 ring-yellow-200",
  info: "text-blue-700 bg-blue-50 ring-blue-200",
};

function Field({
  label,
  value,
  href,
}: {
  label: string;
  value: string | null | undefined;
  // When the linked record has a page of its own, the id becomes the way in.
  href?: string;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-gray-500">
        {label}
      </dt>
      <dd className="mt-1 break-all font-mono text-sm text-gray-900">
        {value == null ? (
          <span className="font-sans italic text-gray-400">none</span>
        ) : href ? (
          <Link href={href} className="text-blue-600 hover:underline">
            {value}
          </Link>
        ) : (
          value
        )}
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

export default function AlertDetailPage({
  params,
}: {
  // Next 16 passes params as a promise; `use()` unwraps it in a client component.
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: alert, isLoading, error } = useAlert(id);

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href="/alerts"
        className="mb-4 inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to alerts
      </Link>

      {isLoading ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Loading alert…</p>
        </div>
      ) : error ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-red-600">
            Could not load this alert: {(error as Error).message}
          </p>
        </div>
      ) : !alert ? (
        <div className="rounded-lg bg-white p-6 shadow">
          <p className="text-gray-600">Alert not found.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div
            className={`rounded-lg p-5 shadow ring-1 ${
              SEVERITY_STYLES[alert.severity] ?? SEVERITY_STYLES.info
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <h1 className="text-xl font-bold">{alert.title}</h1>
              <div className="flex shrink-0 gap-2">
                <span className="rounded-full bg-white/70 px-2 py-1 text-xs font-medium capitalize">
                  {alert.severity}
                </span>
                <span className="rounded-full bg-white/70 px-2 py-1 text-xs font-medium capitalize">
                  {alert.category}
                </span>
              </div>
            </div>
            <p className="mt-2 text-sm">{alert.description}</p>
            <p className="mt-3 text-xs opacity-75">
              Detected {new Date(alert.created_at).toLocaleString()}
              {alert.resolved_at
                ? ` · resolved ${new Date(alert.resolved_at).toLocaleString()}`
                : " · unresolved"}
            </p>
          </div>

          {alert.root_cause && (
            <Section title="Root cause">
              <p className="text-sm text-gray-700">{alert.root_cause}</p>
            </Section>
          )}

          {alert.suggested_fix && (
            <Section title="Suggested fix">
              <p className="text-sm text-gray-700">{alert.suggested_fix}</p>
            </Section>
          )}

          {/*
            The point of this page: which record produced the alert. A polling
            diagnosis carries an execution, a heartbeat miss carries a monitor,
            a vault alert carries a credential — so which of these is populated
            tells you which detection path fired.
          */}
          <Section title="Linked records">
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="Execution"
                value={alert.execution_id}
                href={
                  alert.execution_id
                    ? `/executions/${alert.execution_id}`
                    : undefined
                }
              />
              <Field label="Monitor" value={alert.monitor_id} />
              <Field label="Integration" value={alert.integration_id} />
              <Field label="Credential" value={alert.credential_id} />
            </dl>
          </Section>

          <Section title="Alert">
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Alert ID" value={alert.id} />
              <Field
                label="Acknowledged"
                value={
                  alert.acknowledged_at
                    ? `${new Date(alert.acknowledged_at).toLocaleString()}${
                        alert.acknowledged_by ? ` by ${alert.acknowledged_by}` : ""
                      }`
                    : null
                }
              />
            </dl>
          </Section>
        </div>
      )}
    </div>
  );
}
