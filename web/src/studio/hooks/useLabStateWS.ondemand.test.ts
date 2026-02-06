/**
 * Tests for useLabStateWS hook handling of image sync fields.
 *
 * Verifies that WebSocket messages containing image_sync_status and
 * image_sync_message are correctly mapped into the hook's nodeStates.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLabStateWS } from "./useLabStateWS";

// Reuse the MockWebSocket pattern from the existing test file
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

  static clearInstances(): void {
    MockWebSocket.instances = [];
  }

  send(data: string): void {
    this.sentMessages.push(data);
  }

  close(_code?: number, _reason?: string): void {
    this.readyState = MockWebSocket.CLOSED;
  }

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
}

vi.useFakeTimers();

describe("useLabStateWS - Image Sync Fields", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", MockWebSocket);
    MockWebSocket.clearInstances();
    vi.clearAllTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    MockWebSocket.clearInstances();
  });

  it("maps image_sync_status from WS message to nodeStates", () => {
    const { result } = renderHook(() => useLabStateWS("test-lab"));

    const ws = MockWebSocket.getLastInstance();
    act(() => {
      ws?.simulateOpen();
    });

    // Send initial state with image_sync_status
    act(() => {
      ws?.simulateMessage({
        type: "initial_state",
        timestamp: new Date().toISOString(),
        data: {
          nodes: [
            {
              node_id: "ceos-2",
              node_name: "ceos-2",
              desired_state: "running",
              actual_state: "undeployed",
              is_ready: false,
              image_sync_status: "syncing",
              image_sync_message: "Pushing ceos:4.28.0F...",
            },
          ],
        },
      });
    });

    const nodeState = result.current.nodeStates.get("ceos-2");
    expect(nodeState).toBeDefined();
    expect(nodeState?.image_sync_status).toBe("syncing");
    expect(nodeState?.image_sync_message).toBe("Pushing ceos:4.28.0F...");
  });

  it("clears image_sync_status when set to null", () => {
    const { result } = renderHook(() => useLabStateWS("test-lab"));

    const ws = MockWebSocket.getLastInstance();
    act(() => {
      ws?.simulateOpen();
    });

    // Send node with syncing status
    act(() => {
      ws?.simulateMessage({
        type: "node_state",
        timestamp: new Date().toISOString(),
        data: {
          node_id: "ceos-2",
          node_name: "ceos-2",
          desired_state: "running",
          actual_state: "undeployed",
          is_ready: false,
          image_sync_status: "syncing",
          image_sync_message: "Syncing...",
        },
      });
    });

    expect(result.current.nodeStates.get("ceos-2")?.image_sync_status).toBe(
      "syncing"
    );

    // Send update clearing sync status
    act(() => {
      ws?.simulateMessage({
        type: "node_state",
        timestamp: new Date().toISOString(),
        data: {
          node_id: "ceos-2",
          node_name: "ceos-2",
          desired_state: "running",
          actual_state: "starting",
          is_ready: false,
          image_sync_status: null,
          image_sync_message: null,
        },
      });
    });

    const nodeState = result.current.nodeStates.get("ceos-2");
    expect(nodeState?.image_sync_status).toBeNull();
    expect(nodeState?.image_sync_message).toBeNull();
    expect(nodeState?.actual_state).toBe("starting");
  });

  it("handles syncing -> starting state transition via WS", () => {
    const onNodeStateChange = vi.fn();
    const { result } = renderHook(() =>
      useLabStateWS("test-lab", { onNodeStateChange })
    );

    const ws = MockWebSocket.getLastInstance();
    act(() => {
      ws?.simulateOpen();
    });

    // Step 1: Node enters syncing
    act(() => {
      ws?.simulateMessage({
        type: "node_state",
        timestamp: new Date().toISOString(),
        data: {
          node_id: "ceos-2",
          node_name: "ceos-2",
          desired_state: "running",
          actual_state: "undeployed",
          is_ready: false,
          image_sync_status: "syncing",
          image_sync_message: "Syncing ceos:4.28.0F...",
        },
      });
    });

    expect(onNodeStateChange).toHaveBeenCalledWith(
      "ceos-2",
      expect.objectContaining({
        image_sync_status: "syncing",
        actual_state: "undeployed",
      })
    );

    // Step 2: Sync completes, node transitions to starting
    act(() => {
      ws?.simulateMessage({
        type: "node_state",
        timestamp: new Date().toISOString(),
        data: {
          node_id: "ceos-2",
          node_name: "ceos-2",
          desired_state: "running",
          actual_state: "starting",
          is_ready: false,
          image_sync_status: null,
          image_sync_message: null,
        },
      });
    });

    expect(onNodeStateChange).toHaveBeenCalledWith(
      "ceos-2",
      expect.objectContaining({
        image_sync_status: null,
        actual_state: "starting",
      })
    );

    // Final state in the map
    const finalState = result.current.nodeStates.get("ceos-2");
    expect(finalState?.actual_state).toBe("starting");
    expect(finalState?.image_sync_status).toBeNull();
  });
});
