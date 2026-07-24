/**
 * TanStack Query hooks for backend API.
 *
 * Hooks for all WRP endpoints. Every request carries the caller's Clerk
 * session token as `Authorization: Bearer <token>` so the backend's
 * get_current_workspace dependency can identify the workspace.
 */

import { useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

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
  severity: "info" | "warning" | "critical";
  category: string;
  title: string;
  description: string;
  root_cause?: string | null;
  suggested_fix?: string | null;
  created_at: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
}

export interface ExecutionRead {
  id: string;
  platform: string;
  status: string;
  workflow_name?: string;
  items_processed?: number;
  error_message?: string;
  created_at: string;
}

export interface MonitorRead {
  id: string;
  name: string;
  monitor_type: string;
  last_status: string;
  last_ping_at?: string | null;
  ping_url?: string | null;
  grace_period_s: number;
  alert_on_miss: boolean;
}

export interface MonitorCreate {
  name: string;
  monitor_type: string;
  integration_id?: string;
  grace_period_s?: number;
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