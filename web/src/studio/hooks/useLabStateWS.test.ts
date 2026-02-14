/**
 * Tests for useLabStateWS hook.
 *
 * These tests verify:
 * 1. Initial connection success
 * 2. Reconnection on close with exponential backoff
 * 3. Reconnect attempts counter increments
 * 4. Ping interval keeps connection alive
 * 5. State reset on lab change
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useLabStateWS } from "./useLabStateWS";

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState: number;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  private static instances: MockWebSocket[] = [];
  private sentMessages: string[] = [];

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    MockWebSocket.instances.push(this);
  }

  static getLastInstance(): MockWebSocket | undefined {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  static getAllInstances(): MockWebSocket[] {
    return MockWebSocket.instances;
  }

  static clearInstances(): void {
    MockWebSocket.instances = [];
  }

  send(data: string): void {
    this.sentMessages.push(data);
  }

  getSentMessages(): string[] {
    return this.sentMessages;
  }

  close(_code?: number, _reason?: string): void {
    this.readyState = MockWebSocket.CLOSED;
  }

  // Test helpers to simulate events
  simulateOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) {
      this.onopen(new Event("open"));
    }
  }

  simulateMessage(data: unknown): void {
    if (this.onmessage) {
      this.onmessage(
        new MessageEvent("message", { data: JSON.stringify(data) })
      );
    }
  }

  simulateClose(code = 1000, reason = ""): void {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) {
      this.onclose(new CloseEvent("close", { code, reason }));
    }
  }

  simulateError(): void {
    if (this.onerror) {
      this.onerror(new Event("error"));
    }
  }
}

// Mock timers
vi.useFakeTimers();

describe("useLabStateWS", () => {
  beforeEach(() => {
    // Replace global WebSocket with mock
    vi.stubGlobal("WebSocket", MockWebSocket);
    MockWebSocket.clearInstances();
    vi.clearAllTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    MockWebSocket.clearInstances();
  });

  describe("Initial Connection", () => {
    it("connects to WebSocket when labId is provided", () => {
      renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      expect(ws).toBeDefined();
      expect(ws?.url).toContain("/ws/labs/test-lab/state");
    });

    it("does not connect when labId is null", () => {
      renderHook(() => useLabStateWS(null));

      expect(MockWebSocket.getAllInstances().length).toBe(0);
    });

    it("does not connect when enabled is false", () => {
      renderHook(() => useLabStateWS("test-lab", { enabled: false }));

      expect(MockWebSocket.getAllInstances().length).toBe(0);
    });

    it("sets isConnected to true on open", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      expect(result.current.isConnected).toBe(false);

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      expect(result.current.isConnected).toBe(true);
    });

    it("resets reconnectAttempts on successful connection", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      // Simulate reconnection scenario
      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateClose();
      });

      // After close, wait for reconnect
      act(() => {
        vi.advanceTimersByTime(1000);
      });

      expect(result.current.reconnectAttempts).toBeGreaterThan(0);

      // New connection opens
      const ws2 = MockWebSocket.getLastInstance();
      act(() => {
        ws2?.simulateOpen();
      });

      expect(result.current.reconnectAttempts).toBe(0);
    });
  });

  describe("Reconnection with Exponential Backoff", () => {
    it("attempts to reconnect after close", async () => {
      renderHook(() => useLabStateWS("test-lab"));

      const initialInstanceCount = MockWebSocket.getAllInstances().length;

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateClose();
      });

      // Advance timer past first reconnect delay (1000ms base)
      act(() => {
        vi.advanceTimersByTime(1000);
      });

      expect(MockWebSocket.getAllInstances().length).toBeGreaterThan(
        initialInstanceCount
      );
    });

    it("uses exponential backoff for reconnection delays", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      const ws1 = MockWebSocket.getLastInstance();
      act(() => {
        ws1?.simulateOpen();
        ws1?.simulateClose();
      });

      // reconnectAttempts is 0 before the first reconnect fires
      expect(result.current.reconnectAttempts).toBe(0);
      expect(MockWebSocket.getAllInstances().length).toBe(1);

      // First reconnect after 1000ms (1s * 2^0)
      act(() => {
        vi.advanceTimersByTime(1001);
      });
      expect(result.current.reconnectAttempts).toBe(1);
      expect(MockWebSocket.getAllInstances().length).toBe(2);

      // Simulate close again (without opening, so reconnectAttempts stays at 1)
      const ws2 = MockWebSocket.getLastInstance();
      act(() => {
        ws2?.simulateClose();
      });

      // Second reconnect after 2000ms (1s * 2^1)
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(MockWebSocket.getAllInstances().length).toBe(2);

      act(() => {
        vi.advanceTimersByTime(600);
      });
      expect(MockWebSocket.getAllInstances().length).toBe(3);
      expect(result.current.reconnectAttempts).toBe(2);
    });

    it("caps backoff delay at 30 seconds", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      // Verify max backoff is 30 seconds
      // After many reconnects, delay should cap at 30000ms
      const ws1 = MockWebSocket.getLastInstance();
      act(() => {
        ws1?.simulateOpen();
        ws1?.simulateClose();
      });

      // Simulate 6 failed reconnects without opening
      // This should hit the 30s cap: 1s, 2s, 4s, 8s, 16s, 32s -> capped to 30s
      for (let i = 0; i < 5; i++) {
        act(() => {
          vi.advanceTimersByTime(30001);
        });
        const ws = MockWebSocket.getLastInstance();
        act(() => {
          ws?.simulateClose();
        });
      }

      // At this point backoff should be capped
      const instancesBefore = MockWebSocket.getAllInstances().length;

      // Advance 29 seconds - should NOT trigger reconnect (capped at 30s)
      act(() => {
        vi.advanceTimersByTime(29000);
      });
      expect(MockWebSocket.getAllInstances().length).toBe(instancesBefore);

      // Advance 2 more seconds - should trigger reconnect
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(MockWebSocket.getAllInstances().length).toBe(instancesBefore + 1);
      expect(result.current.reconnectAttempts).toBeGreaterThan(0);
    });
  });

  describe("Reconnect Attempts Counter", () => {
    it("increments counter on each reconnection attempt", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      expect(result.current.reconnectAttempts).toBe(0);

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateClose();
      });

      act(() => {
        vi.advanceTimersByTime(1001);
      });

      expect(result.current.reconnectAttempts).toBe(1);

      // Simulate another close
      const ws2 = MockWebSocket.getLastInstance();
      act(() => {
        ws2?.simulateClose();
      });

      act(() => {
        vi.advanceTimersByTime(2001);
      });

      expect(result.current.reconnectAttempts).toBe(2);
    });
  });

  describe("Ping/Pong Keep-Alive", () => {
    it("starts ping interval on connection open", async () => {
      renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      // Advance past ping interval (25 seconds)
      act(() => {
        vi.advanceTimersByTime(25001);
      });

      const messages = ws?.getSentMessages() || [];
      const pingMessages = messages.filter((m) => m.includes("ping"));
      expect(pingMessages.length).toBeGreaterThanOrEqual(1);
    });

    it("handles pong messages without error", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      // Should not throw
      act(() => {
        ws?.simulateMessage({ type: "pong", timestamp: new Date().toISOString() });
      });

      expect(result.current.isConnected).toBe(true);
    });

    it("handles heartbeat messages without error", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      // Should not throw
      act(() => {
        ws?.simulateMessage({
          type: "heartbeat",
          timestamp: new Date().toISOString(),
        });
      });

      expect(result.current.isConnected).toBe(true);
    });
  });

  describe("State Reset on Lab Change", () => {
    it("clears node states when lab changes", async () => {
      const { result, rerender } = renderHook(
        ({ labId }) => useLabStateWS(labId),
        { initialProps: { labId: "lab-1" } }
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "initial_state",
          timestamp: new Date().toISOString(),
          data: {
            nodes: [
              {
                node_id: "n1",
                node_name: "R1",
                desired_state: "running",
                actual_state: "running",
                is_ready: true,
              },
            ],
          },
        });
      });

      expect(result.current.nodeStates.size).toBe(1);

      // Change lab
      rerender({ labId: "lab-2" });

      expect(result.current.nodeStates.size).toBe(0);
    });

    it("clears link states when lab changes", async () => {
      const { result, rerender } = renderHook(
        ({ labId }) => useLabStateWS(labId),
        { initialProps: { labId: "lab-1" } }
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "initial_links",
          timestamp: new Date().toISOString(),
          data: {
            links: [
              {
                link_name: "R1:eth1-R2:eth1",
                desired_state: "up",
                actual_state: "up",
                source_node: "R1",
                target_node: "R2",
              },
            ],
          },
        });
      });

      expect(result.current.linkStates.size).toBe(1);

      // Change lab
      rerender({ labId: "lab-2" });

      expect(result.current.linkStates.size).toBe(0);
    });

    it("resets reconnect counter when lab changes", async () => {
      const { result, rerender } = renderHook(
        ({ labId }) => useLabStateWS(labId),
        { initialProps: { labId: "lab-1" } }
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateClose();
      });

      act(() => {
        vi.advanceTimersByTime(1001);
      });

      expect(result.current.reconnectAttempts).toBeGreaterThan(0);

      // Change lab
      rerender({ labId: "lab-2" });

      expect(result.current.reconnectAttempts).toBe(0);
    });

    it("clears lab state when lab changes", async () => {
      const { result, rerender } = renderHook(
        ({ labId }) => useLabStateWS(labId),
        { initialProps: { labId: "lab-1" } }
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "lab_state",
          timestamp: new Date().toISOString(),
          data: { lab_id: "lab-1", state: "running" },
        });
      });

      expect(result.current.labState).not.toBeNull();

      // Change lab
      rerender({ labId: "lab-2" });

      expect(result.current.labState).toBeNull();
    });
  });

  describe("Message Handling", () => {
    it("updates node states on node_state message", async () => {
      const onNodeStateChange = vi.fn();
      const { result } = renderHook(() =>
        useLabStateWS("test-lab", { onNodeStateChange })
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "node_state",
          timestamp: new Date().toISOString(),
          data: {
            node_id: "n1",
            node_name: "R1",
            desired_state: "running",
            actual_state: "running",
            is_ready: true,
          },
        });
      });

      expect(result.current.nodeStates.get("n1")).toBeDefined();
      expect(onNodeStateChange).toHaveBeenCalledWith("n1", expect.any(Object));
    });

    it("updates link states on link_state message", async () => {
      const onLinkStateChange = vi.fn();
      const { result } = renderHook(() =>
        useLabStateWS("test-lab", { onLinkStateChange })
      );

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "link_state",
          timestamp: new Date().toISOString(),
          data: {
            link_name: "R1:eth1-R2:eth1",
            desired_state: "up",
            actual_state: "up",
            source_node: "R1",
            target_node: "R2",
          },
        });
      });

      expect(result.current.linkStates.get("R1:eth1-R2:eth1")).toBeDefined();
      expect(onLinkStateChange).toHaveBeenCalledWith(
        "R1:eth1-R2:eth1",
        expect.any(Object)
      );
    });

    it("calls onLabStateChange callback on lab_state message", async () => {
      const onLabStateChange = vi.fn();
      renderHook(() => useLabStateWS("test-lab", { onLabStateChange }));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "lab_state",
          timestamp: new Date().toISOString(),
          data: { lab_id: "test-lab", state: "running" },
        });
      });

      expect(onLabStateChange).toHaveBeenCalledWith(
        expect.objectContaining({ lab_id: "test-lab" })
      );
    });

    it("calls onJobProgress callback on job_progress message", async () => {
      const onJobProgress = vi.fn();
      renderHook(() => useLabStateWS("test-lab", { onJobProgress }));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateMessage({
          type: "job_progress",
          timestamp: new Date().toISOString(),
          data: {
            job_id: "job-1",
            action: "deploy",
            status: "running",
            progress_message: "Deploying nodes...",
          },
        });
      });

      expect(onJobProgress).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: "job-1" })
      );
    });
  });

  describe("Refresh Function", () => {
    it("sends refresh message when called", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      act(() => {
        result.current.refresh();
      });

      const messages = ws?.getSentMessages() || [];
      const refreshMessages = messages.filter((m) => m.includes("refresh"));
      expect(refreshMessages.length).toBeGreaterThanOrEqual(1);
    });

    it("does nothing when WebSocket is not open", async () => {
      const { result } = renderHook(() => useLabStateWS("test-lab"));

      // Don't simulate open - WebSocket is still connecting
      act(() => {
        result.current.refresh();
      });

      const ws = MockWebSocket.getLastInstance();
      const messages = ws?.getSentMessages() || [];
      expect(messages.length).toBe(0);
    });
  });

  describe("WebSocket Token Authentication", () => {
    it("includes token in WebSocket URL when present in localStorage", () => {
      vi.stubGlobal("localStorage", {
        getItem: (key: string) => (key === "token" ? "my-jwt-token" : null),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      });

      renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      expect(ws?.url).toContain("?token=my-jwt-token");
    });

    it("omits token param when localStorage has no token", () => {
      vi.stubGlobal("localStorage", {
        getItem: (_key: string) => null,
        setItem: vi.fn(),
        removeItem: vi.fn(),
      });

      renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      expect(ws?.url).not.toContain("?token=");
      expect(ws?.url).not.toContain("token=");
    });
  });

  describe("Cleanup on Unmount", () => {
    it("closes WebSocket on unmount", async () => {
      const { unmount } = renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
      });

      unmount();

      expect(ws?.readyState).toBe(MockWebSocket.CLOSED);
    });

    it("clears reconnect timeout on unmount", async () => {
      const { unmount } = renderHook(() => useLabStateWS("test-lab"));

      const ws = MockWebSocket.getLastInstance();
      act(() => {
        ws?.simulateOpen();
        ws?.simulateClose();
      });

      // Unmount before reconnect triggers
      unmount();

      // Advancing timers should not create new WebSocket
      const instanceCount = MockWebSocket.getAllInstances().length;
      act(() => {
        vi.advanceTimersByTime(5000);
      });

      expect(MockWebSocket.getAllInstances().length).toBe(instanceCount);
    });
  });
});
