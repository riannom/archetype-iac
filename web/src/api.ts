export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

function buildQueryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const queryParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      queryParams.set(key, String(value));
    }
  }
  const queryString = queryParams.toString();
  return queryString ? `?${queryString}` : "";
}

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
  const queryString = buildQueryString({
    service: params.service,
    level: params.level,
    since: params.since,
    search: params.search,
    limit: params.limit,
  });
  const path = `/logs${queryString}`;

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


// --- Lab Logs Types and Functions ---

export interface LabLogEntry {
  timestamp: string;
  level: "info" | "success" | "warning" | "error";
  message: string;
  host_id?: string | null;
  host_name?: string | null;
  job_id?: string | null;
  source: "job" | "system" | "realtime";
}

export interface LabLogJob {
  id: string;
  action: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
}

export interface LabLogsResponse {
  entries: LabLogEntry[];
  jobs: LabLogJob[];
  hosts: string[];
  total_count: number;
  error_count: number;
  has_more: boolean;
}

export interface LabLogsQueryParams {
  job_id?: string;
  host_id?: string;
  level?: string;
  since?: string;
  search?: string;
  limit?: number;
}

export async function getLabLogs(
  labId: string,
  params: LabLogsQueryParams = {}
): Promise<LabLogsResponse> {
  const queryString = buildQueryString({
    job_id: params.job_id,
    host_id: params.host_id,
    level: params.level,
    since: params.since,
    search: params.search,
    limit: params.limit,
  });
  const path = `/labs/${labId}/logs${queryString}`;

  return apiRequest<LabLogsResponse>(path);
}
