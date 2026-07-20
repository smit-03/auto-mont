/**
 * TanStack Query hooks for backend API.
 *
 * Hooks for all WRP endpoints.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// Generic API client
export async function fetcher<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || "API request failed");
  }

  return response.json();
}

// Integration hooks
export function useIntegrations() {
  return useQuery({
    queryKey: ["integrations"],
    queryFn: () => fetcher<IntegrationRead[]>("/integrations"),
  });
}

export function useIntegration(id: string) {
  return useQuery({
    queryKey: ["integrations", id],
    queryFn: () => fetcher<IntegrationRead>(`/integrations/${id}`),
    enabled: !!id,
  });
}

export function useCreateIntegration() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: IntegrationCreate) =>
      fetcher<IntegrationRead>("/integrations", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["integrations"] }),
  });
}

// Alert hooks
export function useAlerts(filters?: { severity?: string; category?: string }) {
  return useQuery({
    queryKey: ["alerts", filters],
    queryFn: () =>
      fetcher<AlertRead[]>("/alerts" + (filters ? `?severity=${filters.severity}` : "")),
  });
}

// Execution hooks
export function useExecutions() {
  return useQuery({
    queryKey: ["executions"],
    queryFn: () => fetcher<ExecutionRead[]>("/executions?limit=50"),
  });
}

// Monitor hooks
export function useMonitors() {
  return useQuery({
    queryKey: ["monitors"],
    queryFn: () => fetcher<MonitorRead[]>("/monitors"),
  });
}

export function useCreateMonitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: MonitorCreate) =>
      fetcher<MonitorRead>("/monitors", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["monitors"] }),
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
}

export interface MonitorCreate {
  name: string;
  monitor_type: string;
  integration_id?: string;
  grace_period_s?: number;
}