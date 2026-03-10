import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  apiRequest,
  rawApiRequest,
  getSystemLogs,
  getLabLogs,
  getVersionInfo,
  checkForUpdates,
  getLoginDefaults,
  getInfrastructureSettings,
  updateInfrastructureSettings,
  getLinkDetail,
  getLabInterfaceMappings,
  getLabInfraNotifications,
  createSupportBundle,
  getSupportBundle,
  listSupportBundles,
  API_BASE_URL,
} from "./api";

// Mock fetch globally
const mockFetch = vi.fn();
const originalFetch = globalThis.fetch;

describe("api", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    globalThis.fetch = mockFetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe("apiRequest", () => {
    it("makes request to correct URL", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ data: "test" }),
      });

      await apiRequest("/test-endpoint");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/test-endpoint`,
        expect.any(Object)
      );
    });

    it("includes Content-Type header", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

      await apiRequest("/test");

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            "Content-Type": "application/json",
          }),
        })
      );
    });

    it("includes Authorization header when token exists", async () => {
      localStorage.setItem("token", "test-jwt-token");

      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

      await apiRequest("/test");

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer test-jwt-token",
          }),
        })
      );
    });

    it("does not include Authorization header when no token", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

      await apiRequest("/test");

      const callArgs = mockFetch.mock.calls[0][1];
      expect(callArgs.headers.Authorization).toBeUndefined();
    });

    it("returns parsed JSON response", async () => {
      const responseData = { id: 1, name: "Test" };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(responseData),
      });

      const result = await apiRequest<typeof responseData>("/test");

      expect(result).toEqual(responseData);
    });

    it("returns empty object for 204 No Content", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 204,
      });

      const result = await apiRequest("/test");

      expect(result).toEqual({});
    });

    it("throws Unauthorized error for 401 response", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: () => Promise.resolve("Unauthorized"),
      });

      await expect(apiRequest("/test")).rejects.toThrow("Unauthorized");
    });

    it("throws error with message for other failed responses", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: () => Promise.resolve("Internal Server Error"),
      });

      await expect(apiRequest("/test")).rejects.toThrow("Internal Server Error");
    });

    it("throws generic error when no message returned", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: () => Promise.resolve(""),
      });

      await expect(apiRequest("/test")).rejects.toThrow("Request failed");
    });

    it("passes through additional options", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

      await apiRequest("/test", {
        method: "POST",
        body: JSON.stringify({ data: "test" }),
      });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ data: "test" }),
        })
      );
    });

    it("merges custom headers with default headers", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

      await apiRequest("/test", {
        headers: { "X-Custom-Header": "custom-value" },
      });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            "X-Custom-Header": "custom-value",
          }),
        })
      );
    });
  });

  // ---- rawApiRequest ----

  describe("rawApiRequest", () => {
    it("includes Bearer token when present in localStorage", async () => {
      localStorage.setItem("token", "raw-jwt");
      mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });

      await rawApiRequest("/some/path");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/some/path`,
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer raw-jwt",
          }),
        })
      );
    });

    it("does not set Content-Type header", async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, status: 200 });

      await rawApiRequest("/binary");

      const callArgs = mockFetch.mock.calls[0][1];
      expect(callArgs.headers["Content-Type"]).toBeUndefined();
    });

    it("returns the raw Response object", async () => {
      const fakeResponse = { ok: true, status: 200, body: "raw" };
      mockFetch.mockResolvedValueOnce(fakeResponse);

      const result = await rawApiRequest("/raw");

      expect(result).toBe(fakeResponse);
    });
  });

  // ---- getSystemLogs ----

  describe("getSystemLogs", () => {
    it("calls correct endpoint with no params", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], total_count: 0, has_more: false }),
      });

      await getSystemLogs();

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/logs`,
        expect.any(Object)
      );
    });

    it("includes service param in query string", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], total_count: 0, has_more: false }),
      });

      await getSystemLogs({ service: "api" });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("service=api"),
        expect.any(Object)
      );
    });

    it("includes level param in query string", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], total_count: 0, has_more: false }),
      });

      await getSystemLogs({ level: "ERROR" });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("level=ERROR"),
        expect.any(Object)
      );
    });

    it("includes multiple params in query string", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], total_count: 0, has_more: false }),
      });

      await getSystemLogs({
        service: "worker",
        level: "WARNING",
        limit: 50,
      });

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain("service=worker");
      expect(url).toContain("level=WARNING");
      expect(url).toContain("limit=50");
    });
  });

  // ---- getLabLogs ----

  describe("getLabLogs", () => {
    it("calls correct endpoint with no params", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], jobs: [], hosts: [], total_count: 0, error_count: 0, has_more: false }),
      });

      await getLabLogs("lab-1");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/labs/lab-1/logs`,
        expect.any(Object)
      );
    });

    it("includes multiple params in query string", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ entries: [], jobs: [], hosts: [], total_count: 0, error_count: 0, has_more: false }),
      });

      await getLabLogs("lab-1", {
        job_id: "job-1",
        level: "error",
        limit: 25,
      });

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain("/labs/lab-1/logs?");
      expect(url).toContain("job_id=job-1");
      expect(url).toContain("level=error");
      expect(url).toContain("limit=25");
    });
  });

  // ---- getVersionInfo ----

  describe("getVersionInfo", () => {
    it("calls /system/version and returns version data", async () => {
      const data = { version: "1.2.3", build_time: "2026-01-01T00:00:00Z" };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getVersionInfo();

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/system/version`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- checkForUpdates ----

  describe("checkForUpdates", () => {
    it("calls /system/updates and returns update info", async () => {
      const data = {
        current_version: "1.0.0",
        latest_version: "1.1.0",
        update_available: true,
        release_url: "https://example.com/release",
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await checkForUpdates();

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/system/updates`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- getLoginDefaults ----

  describe("getLoginDefaults", () => {
    it("calls /system/login-defaults", async () => {
      const data = {
        dark_theme_id: "dark-1",
        dark_background_id: "bg-1",
        dark_background_opacity: 0.5,
        light_theme_id: "light-1",
        light_background_id: "bg-2",
        light_background_opacity: 0.8,
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getLoginDefaults();

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/system/login-defaults`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- getInfrastructureSettings ----

  describe("getInfrastructureSettings", () => {
    it("calls /infrastructure/settings", async () => {
      const data = {
        overlay_mtu: 1450,
        mtu_verification_enabled: true,
        overlay_preserve_container_mtu: false,
        overlay_clamp_host_mtu: false,
        login_dark_theme_id: "dark-1",
        login_dark_background_id: "bg-1",
        login_dark_background_opacity: 0.5,
        login_light_theme_id: "light-1",
        login_light_background_id: "bg-2",
        login_light_background_opacity: 0.8,
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getInfrastructureSettings();

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/infrastructure/settings`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- updateInfrastructureSettings ----

  describe("updateInfrastructureSettings", () => {
    it("sends PATCH request with payload", async () => {
      const payload = { overlay_mtu: 9000 };
      const responseData = { ...payload, mtu_verification_enabled: true };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(responseData),
      });

      const result = await updateInfrastructureSettings(payload);

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/infrastructure/settings`,
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify(payload),
        })
      );
      expect(result).toEqual(responseData);
    });
  });

  // ---- getLinkDetail ----

  describe("getLinkDetail", () => {
    it("encodes link name in URL", async () => {
      const data = {
        link_name: "R1:eth1-R2:eth1",
        actual_state: "up",
        desired_state: "up",
        error_message: null,
        is_cross_host: false,
        source: {},
        target: {},
        tunnel: null,
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      await getLinkDetail("lab-1", "R1:eth1-R2:eth1");

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain("/labs/lab-1/links/");
      expect(url).toContain(encodeURIComponent("R1:eth1-R2:eth1"));
      expect(url).toContain("/detail");
    });
  });

  // ---- getLabInterfaceMappings ----

  describe("getLabInterfaceMappings", () => {
    it("calls correct endpoint with lab id", async () => {
      const data = { mappings: [], total: 0 };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getLabInterfaceMappings("lab-42");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/labs/lab-42/interface-mappings`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- getLabInfraNotifications ----

  describe("getLabInfraNotifications", () => {
    it("calls correct endpoint", async () => {
      const data = { notifications: [] };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getLabInfraNotifications("lab-99");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/labs/lab-99/infra/notifications`,
        expect.any(Object)
      );
      expect(result).toEqual(data);
    });
  });

  // ---- createSupportBundle ----

  describe("createSupportBundle", () => {
    it("sends POST with full payload", async () => {
      const payload = {
        summary: "test bug",
        repro_steps: "step 1",
        expected_behavior: "works",
        actual_behavior: "broken",
        time_window_hours: 24,
        impacted_lab_ids: ["lab-1"],
        impacted_agent_ids: ["agent-1"],
        include_configs: true,
        pii_safe: false,
      };
      const responseData = { id: "bundle-1", status: "pending", ...payload, user_id: "u1", created_at: "2026-01-01" };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(responseData),
      });

      const result = await createSupportBundle(payload);

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/support-bundles`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify(payload),
        })
      );
      expect(result.id).toBe("bundle-1");
    });
  });

  // ---- getSupportBundle ----

  describe("getSupportBundle", () => {
    it("calls correct endpoint with bundle id", async () => {
      const data = { id: "bundle-5", status: "completed", user_id: "u1", include_configs: true, pii_safe: true, time_window_hours: 12, created_at: "2026-01-01" };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(data),
      });

      const result = await getSupportBundle("bundle-5");

      expect(mockFetch).toHaveBeenCalledWith(
        `${API_BASE_URL}/support-bundles/bundle-5`,
        expect.any(Object)
      );
      expect(result.id).toBe("bundle-5");
    });
  });

  // ---- listSupportBundles ----

  describe("listSupportBundles", () => {
    it("includes default limit in query string", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      });

      await listSupportBundles();

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain("limit=20");
    });

    it("uses custom limit when provided", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      });

      await listSupportBundles(50);

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain("limit=50");
    });
  });

  // ---- Error handling ----

  describe("error handling", () => {
    it("throws Unauthorized for 401 responses", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: () => Promise.resolve("Unauthorized"),
      });

      await expect(apiRequest("/protected")).rejects.toThrow("Unauthorized");
    });

    it("throws error text for non-ok responses", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 422,
        text: () => Promise.resolve("Validation Error: field X required"),
      });

      await expect(apiRequest("/validate")).rejects.toThrow(
        "Validation Error: field X required"
      );
    });

    it("throws generic message when response text is empty", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 503,
        text: () => Promise.resolve(""),
      });

      await expect(apiRequest("/down")).rejects.toThrow("Request failed");
    });
  });
});
