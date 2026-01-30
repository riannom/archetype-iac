export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("token");
  const { headers: customHeaders, ...restOptions } = options;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...restOptions,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...((customHeaders as Record<string, string>) || {}),
    },
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


// --- Version and Update Types and Functions ---

export interface VersionInfo {
  version: string;
  build_time?: string | null;
}

export interface UpdateInfo {
  current_version: string;
  latest_version?: string | null;
  update_available: boolean;
  release_url?: string | null;
  release_notes?: string | null;
  published_at?: string | null;
  error?: string | null;
}

export async function getVersionInfo(): Promise<VersionInfo> {
  return apiRequest<VersionInfo>("/system/version");
}

export async function checkForUpdates(): Promise<UpdateInfo> {
  return apiRequest<UpdateInfo>("/system/updates");
}
