/**
 * TanStack Query hooks for backend API.
 *
 * Hooks for all WRP endpoints. Every request carries the caller's Clerk
 * session token as `Authorization: Bearer <token>` so the backend's
 * get_current_workspace dependency can identify the workspace.
 */

import { useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

type Fetcher = <T>(endpoint: string, options?: RequestInit) => Promise<T>;

// Generic API client. `token` is the Clerk session JWT (null when signed out).
export async function fetcher<T>(
  endpoint: string,
  options?: RequestInit,
  token?: string | null
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || "API request failed");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

/**
 * Returns a fetcher bound to the current Clerk session. It calls getToken()
 * per request so a rotated/refreshed token is always used.
 */
export function useAuthedFetcher(): Fetcher {
  const { getToken } = useAuth();

  return useCallback(
    async <T,>(endpoint: string, options?: RequestInit): Promise<T> => {
      const token = await getToken();
      return fetcher<T>(endpoint, options, token);
    },
    [getToken]
  );
}

// Integration hooks
export function useIntegrations() {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["integrations"],
    queryFn: () => api<IntegrationRead[]>("/integrations"),
  });
}

export function useIntegration(id: string) {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["integrations", id],
    queryFn: () => api<IntegrationRead>(`/integrations/${id}`),
    enabled: !!id,
  });
}

export function useCreateIntegration() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: IntegrationCreate) =>
      api<IntegrationRead>("/integrations", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["integrations"] }),
  });
}

// Alert hooks
export function useAlerts(filters?: { severity?: string; category?: string }) {
  const api = useAuthedFetcher();
  const params = new URLSearchParams();
  if (filters?.severity && filters.severity !== "all") {
    params.set("severity", filters.severity);
  }
  if (filters?.category && filters.category !== "all") {
    params.set("category", filters.category);
  }
  const query = params.toString();

  return useQuery({
    queryKey: ["alerts", filters],
    queryFn: () => api<AlertRead[]>("/alerts" + (query ? `?${query}` : "")),
  });
}

export function useAlert(id: string) {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["alerts", id],
    queryFn: () => api<AlertRead>(`/alerts/${id}`),
    enabled: !!id,
  });
}

// Execution hooks
export function useExecutions(filters?: {
  status?: string;
  integration_id?: string;
  workflow_id?: string;
}) {
  const api = useAuthedFetcher();

  return useInfiniteQuery({
    queryKey: ["executions", filters],
    // The cursor is opaque: it comes from the previous page's next_cursor and
    // is passed straight back. Never construct one here.
    queryFn: ({ pageParam }: { pageParam: string | null }) => {
      const params = new URLSearchParams();
      if (filters?.status && filters.status !== "all") {
        params.set("status", filters.status);
      }
      if (filters?.integration_id) {
        params.set("integration_id", filters.integration_id);
      }
      if (filters?.workflow_id) params.set("workflow_id", filters.workflow_id);
      if (pageParam) params.set("cursor", pageParam);
      const query = params.toString();
      return api<CursorPage<ExecutionRead>>(
        "/executions" + (query ? `?${query}` : "")
      );
    },
    initialPageParam: null as string | null,
    // null next_cursor means the last page; returning undefined is how
    // TanStack Query is told there is nothing more to fetch.
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useExecution(id: string) {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["executions", id],
    queryFn: () => api<ExecutionDetail>(`/executions/${id}`),
    enabled: !!id,
  });
}

export function useExecutionDiagnostic(id: string) {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["executions", id, "diagnostic"],
    queryFn: () => api<DiagnosticResult>(`/executions/${id}/diagnostic`),
    enabled: !!id,
    // A healthy run has no diagnosis and the endpoint answers 404. That is the
    // expected outcome, not a transient failure, so do not retry it.
    retry: false,
  });
}

/**
 * Workflows this integration has produced executions for.
 *
 * Backs the monitor form's workflow picker. Only workflows that have run at
 * least once appear — a monitor on a workflow with no history has nothing to
 * assert against yet.
 */
export function useIntegrationWorkflows(integrationId: string | undefined) {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["integrations", integrationId, "workflows"],
    queryFn: () =>
      api<WorkflowSummary[]>(`/integrations/${integrationId}/workflows`),
    enabled: !!integrationId,
  });
}

// Monitor hooks
export function useMonitors() {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["monitors"],
    queryFn: () => api<MonitorRead[]>("/monitors"),
  });
}

export function useCreateMonitor() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: MonitorCreate) =>
      api<MonitorRead>("/monitors", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["monitors"] }),
  });
}

export function useDeleteMonitor() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api<void>(`/monitors/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["monitors"] }),
  });
}

export function useUpdateMonitor() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MonitorUpdate }) =>
      api<MonitorRead>(`/monitors/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["monitors"] }),
  });
}

// Credential hooks
export function useCredentials() {
  const api = useAuthedFetcher();
  return useQuery({
    queryKey: ["credentials"],
    queryFn: () => api<CredentialRead[]>("/credentials"),
  });
}

export function useCreateCredential() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CredentialCreate) =>
      api<CredentialRead>("/credentials", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useDeleteCredential() {
  const api = useAuthedFetcher();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/credentials/${id}`, { method: "DELETE" }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

// Types
export interface IntegrationRead {
  id: string;
  platform: string;
  display_name: string;
  status: string;
  last_polled_at?: string | null;
}

export interface IntegrationCreate {
  platform: string;
  display_name: string;
  api_key: string;
  base_url?: string;
  poll_interval_s?: number;
}

export interface AlertRead {
  id: string;
  workspace_id: string;
  // Which record produced this alert. The backend has always returned these
  // four; the interface omitted them, so the data arrived and was discarded —
  // which is why an alert could not be traced to its cause in the UI.
  integration_id?: string | null;
  execution_id?: string | null;
  monitor_id?: string | null;
  credential_id?: string | null;
  severity: "info" | "warning" | "critical";
  category: string;
  title: string;
  description: string;
  root_cause?: string | null;
  suggested_fix?: string | null;
  auto_remediated?: boolean;
  created_at: string;
  acknowledged_at?: string | null;
  acknowledged_by?: string | null;
  resolved_at?: string | null;
}

/**
 * The envelope used by cursor-paginated endpoints. The rest of the API returns
 * bare arrays; pagination is the exception because a cursor is response-level
 * metadata with nowhere to live inside an array. See DECISIONS.md.
 */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ExecutionRead {
  id: string;
  workspace_id: string;
  integration_id: string;
  platform: string;
  platform_run_id: string;
  workflow_id: string;
  workflow_name?: string | null;
  status: "success" | "error" | "running" | "timeout" | "silent_fail";
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  node_count?: number | null;
  items_processed?: number | null;
  error_message?: string | null;
  error_node?: string | null;
  outcome_valid?: boolean | null;
  outcome_reason?: string | null;
  created_at: string;
}

// raw_payload is the platform's untouched response, so its shape is the
// platform's business, not ours. The detail page reads known n8n paths
// defensively rather than modelling it.
export interface ExecutionDetail extends ExecutionRead {
  raw_payload: Record<string, unknown>;
}

export interface DiagnosticResult {
  root_cause: string;
  category: string;
  confidence: number;
  suggested_fix: string;
  requires_hitl: boolean;
}

export interface MonitorRead {
  id: string;
  name: string;
  monitor_type: string;
  workflow_id?: string | null;
  integration_id?: string | null;
  last_status: string;
  last_ping_at?: string | null;
  ping_url?: string | null;
  grace_period_s: number;
  alert_on_miss: boolean;
}

export interface WorkflowSummary {
  workflow_id: string;
  workflow_name?: string | null;
  last_seen_at: string;
}

export interface MonitorCreate {
  name: string;
  monitor_type: string;
  integration_id?: string;
  // Required by the backend when monitor_type is "outcome" — an outcome
  // monitor with no workflow scope matches nothing.
  workflow_id?: string;
  description?: string;
  grace_period_s?: number;
  expected_outcome?: Record<string, unknown> | null;
}

export interface MonitorUpdate {
  name?: string;
  description?: string;
  grace_period_s?: number;
  expected_cron?: string;
  expected_outcome?: Record<string, unknown> | null;
  alert_on_miss?: boolean;
}

export interface CredentialRead {
  id: string;
  provider: string;
  display_name: string;
  scopes?: string[] | null;
  gcp_status?: string | null;
  expires_at?: string | null;
  last_verified_at?: string | null;
  last_error?: string | null;
  status: string;
  created_at: string;
  hours_until_expiry?: number | null;
  is_expiring_soon: boolean;
}

export interface CredentialCreate {
  provider: string;
  display_name: string;
  integration_id?: string;
  token: string;
  refresh_token?: string;
  expires_at?: string;
  scopes?: string[];
}