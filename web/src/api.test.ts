import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiRequest, getSystemLogs, API_BASE_URL } from "./api";

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
});
