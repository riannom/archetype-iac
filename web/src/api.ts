export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("token");
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("Unauthorized");
    }
    const message = await response.text();
    throw new Error(message || "Request failed");
  }

  if (response.status === 204) {
    return {} as T;
  }

  return (await response.json()) as T;
}


// --- System Logs Types and Functions ---

export interface LogEntry {
  timestamp: string;
  level: string;
  service: string;
  message: string;
  correlation_id?: string | null;
  logger?: string | null;
  extra?: Record<string, unknown> | null;
}

export interface LogQueryResponse {
  entries: LogEntry[];
  total_count: number;
  has_more: boolean;
}

export interface LogQueryParams {
  service?: string;
  level?: string;
  since?: string;
  search?: string;
  limit?: number;
}

export async function getSystemLogs(params: LogQueryParams = {}): Promise<LogQueryResponse> {
  const queryParams = new URLSearchParams();
  if (params.service) queryParams.set("service", params.service);
  if (params.level) queryParams.set("level", params.level);
  if (params.since) queryParams.set("since", params.since);
  if (params.search) queryParams.set("search", params.search);
  if (params.limit) queryParams.set("limit", params.limit.toString());

  const queryString = queryParams.toString();
  const path = `/logs${queryString ? `?${queryString}` : ""}`;

  return apiRequest<LogQueryResponse>(path);
}
