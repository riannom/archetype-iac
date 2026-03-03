import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useConsoleManager } from './useConsoleManager';
import { DeviceType, Node } from '../types';

// ── Test data ──

const createDeviceNode = (id: string, name: string): Node => ({
  id,
  name,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'linux',
  version: 'latest',
  x: 100,
  y: 100,
});

const testNodes: Node[] = [
  createDeviceNode('node-1', 'Router1'),
  createDeviceNode('node-2', 'Router2'),
  createDeviceNode('node-3', 'Switch1'),
];

const noPreferences = null;
const floatingPreferences = { canvas_settings: { consoleInBottomPanel: false } };
const dockedPreferences = { canvas_settings: { consoleInBottomPanel: true } };

// ── Tests ──

describe('useConsoleManager', () => {
  const renderConsoleHook = (
    nodes: Node[] = testNodes,
    preferences: { canvas_settings?: { consoleInBottomPanel?: boolean } } | null = noPreferences,
  ) => {
    return renderHook(
      ({ nodes: n, preferences: p }) => useConsoleManager({ nodes: n, preferences: p }),
      {
        initialProps: { nodes, preferences },
      },
    );
  };

  describe('initialization', () => {
    it('starts with no console windows', () => {
      const { result } = renderConsoleHook();

      expect(result.current.consoleWindows).toEqual([]);
      expect(result.current.dockedConsoles).toEqual([]);
      expect(result.current.activeBottomTabId).toBe('log');
    });
  });

  // ── Floating console windows ──

  describe('floating console windows', () => {
    it('opens a new floating console window for a node', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.consoleWindows[0].deviceIds).toContain('node-1');
      expect(result.current.consoleWindows[0].activeDeviceId).toBe('node-1');
      expect(result.current.consoleWindows[0].isExpanded).toBe(true);
    });

    it('activates existing window when opening same node again', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      // Should not create a second window
      expect(result.current.consoleWindows).toHaveLength(1);
    });

    it('does nothing when opening console for unknown node', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('unknown-node', setIsTaskLogVisible);
      });

      expect(result.current.consoleWindows).toHaveLength(0);
    });

    it('closes a floating console window', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleCloseConsoleWindow(windowId);
      });

      expect(result.current.consoleWindows).toHaveLength(0);
    });

    it('updates console window position', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleUpdateConsoleWindowPos(windowId, 500, 300);
      });

      expect(result.current.consoleWindows[0].x).toBe(500);
      expect(result.current.consoleWindows[0].y).toBe(300);
    });

    it('toggles minimize state', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;
      expect(result.current.consoleWindows[0].isExpanded).toBe(true);

      act(() => {
        result.current.handleToggleMinimize(windowId);
      });

      expect(result.current.consoleWindows[0].isExpanded).toBe(false);

      act(() => {
        result.current.handleToggleMinimize(windowId);
      });

      expect(result.current.consoleWindows[0].isExpanded).toBe(true);
    });
  });

  // ── Tab management ──

  describe('tab management', () => {
    it('sets active tab within a window', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      // Open two nodes — they will be separate windows by default
      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });

      // Merge them into one window
      const win1Id = result.current.consoleWindows[0].id;
      const win2Id = result.current.consoleWindows[1].id;

      act(() => {
        result.current.handleMergeWindows(win2Id, win1Id);
      });

      // Now win1 has both deviceIds
      const mergedWin = result.current.consoleWindows[0];
      expect(mergedWin.deviceIds).toContain('node-1');
      expect(mergedWin.deviceIds).toContain('node-2');

      // Switch active tab
      act(() => {
        result.current.handleSetActiveConsoleTab(mergedWin.id, 'node-2');
      });

      expect(result.current.consoleWindows[0].activeDeviceId).toBe('node-2');
    });

    it('closes a single tab from a multi-tab window', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      // Open two nodes and merge
      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });

      const win1Id = result.current.consoleWindows[0].id;
      const win2Id = result.current.consoleWindows[1].id;

      act(() => {
        result.current.handleMergeWindows(win2Id, win1Id);
      });

      const mergedWinId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleCloseConsoleTab(mergedWinId, 'node-1');
      });

      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.consoleWindows[0].deviceIds).toEqual(['node-2']);
    });

    it('removes window when closing last tab', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleCloseConsoleTab(windowId, 'node-1');
      });

      expect(result.current.consoleWindows).toHaveLength(0);
    });

    it('reorders tabs within a window', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      // Open 3 nodes and merge all into one window
      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-3', setIsTaskLogVisible);
      });

      const win1Id = result.current.consoleWindows[0].id;
      const win2Id = result.current.consoleWindows[1].id;
      const win3Id = result.current.consoleWindows[2].id;

      act(() => {
        result.current.handleMergeWindows(win2Id, win1Id);
      });
      act(() => {
        result.current.handleMergeWindows(win3Id, result.current.consoleWindows[0].id);
      });

      const mergedWinId = result.current.consoleWindows[0].id;

      // Move first tab to third position
      act(() => {
        result.current.handleReorderTab(mergedWinId, 0, 2);
      });

      const reordered = result.current.consoleWindows[0].deviceIds;
      // After removing index 0, adjusted index = 2-1=1, insert at 1
      expect(reordered[0]).toBe('node-2');
      expect(reordered[1]).toBe('node-1');
      expect(reordered[2]).toBe('node-3');
    });
  });

  // ── Window merging and splitting ──

  describe('merge and split', () => {
    it('merges two windows into one', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });

      expect(result.current.consoleWindows).toHaveLength(2);

      const sourceId = result.current.consoleWindows[1].id;
      const targetId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleMergeWindows(sourceId, targetId);
      });

      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.consoleWindows[0].deviceIds).toHaveLength(2);
    });

    it('handles merge with invalid window IDs gracefully', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      act(() => {
        result.current.handleMergeWindows('nonexistent', result.current.consoleWindows[0].id);
      });

      // Nothing should change
      expect(result.current.consoleWindows).toHaveLength(1);
    });

    it('splits a tab from a multi-tab window into a new window', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });

      const win1Id = result.current.consoleWindows[0].id;
      const win2Id = result.current.consoleWindows[1].id;

      act(() => {
        result.current.handleMergeWindows(win2Id, win1Id);
      });

      const mergedWinId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleSplitTab(mergedWinId, 'node-2', 300, 200);
      });

      expect(result.current.consoleWindows).toHaveLength(2);

      const sourceWin = result.current.consoleWindows.find((w) => w.id === mergedWinId);
      expect(sourceWin?.deviceIds).toEqual(['node-1']);

      const newWin = result.current.consoleWindows.find((w) => w.id !== mergedWinId);
      expect(newWin?.deviceIds).toEqual(['node-2']);
      expect(newWin?.x).toBe(300);
      expect(newWin?.y).toBe(200);
    });

    it('does not split when window has only one tab', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleSplitTab(windowId, 'node-1', 300, 200);
      });

      // Should still be one window
      expect(result.current.consoleWindows).toHaveLength(1);
    });

    it('clamps split position to non-negative coordinates', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });

      const win1Id = result.current.consoleWindows[0].id;
      const win2Id = result.current.consoleWindows[1].id;

      act(() => {
        result.current.handleMergeWindows(win2Id, win1Id);
      });

      const mergedWinId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleSplitTab(mergedWinId, 'node-2', -50, -100);
      });

      const newWin = result.current.consoleWindows.find((w) => w.id !== mergedWinId);
      expect(newWin?.x).toBe(0);
      expect(newWin?.y).toBe(0);
    });
  });

  // ── Docked consoles (bottom panel) ──

  describe('docked consoles', () => {
    it('opens console in bottom panel when preference is set', () => {
      const { result } = renderConsoleHook(testNodes, dockedPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      expect(result.current.dockedConsoles).toHaveLength(1);
      expect(result.current.dockedConsoles[0].nodeId).toBe('node-1');
      expect(result.current.dockedConsoles[0].nodeName).toBe('Router1');
      expect(result.current.activeBottomTabId).toBe('node-1');
      expect(setIsTaskLogVisible).toHaveBeenCalledWith(true);
    });

    it('activates existing docked console when opening same node', () => {
      const { result } = renderConsoleHook(testNodes, dockedPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      // Should not duplicate
      expect(result.current.dockedConsoles).toHaveLength(1);
      expect(result.current.activeBottomTabId).toBe('node-1');
    });

    it('closes a docked console', () => {
      const { result } = renderConsoleHook(testNodes, dockedPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      act(() => {
        result.current.handleCloseDockedConsole('node-1');
      });

      expect(result.current.dockedConsoles).toHaveLength(0);
      expect(result.current.activeBottomTabId).toBe('log'); // Reset to log
    });

    it('reorders docked console tabs', () => {
      const { result } = renderConsoleHook(testNodes, dockedPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-2', setIsTaskLogVisible);
      });
      act(() => {
        result.current.handleOpenConsole('node-3', setIsTaskLogVisible);
      });

      // Move first docked tab to third position
      act(() => {
        result.current.handleReorderDockedTab(0, 2);
      });

      expect(result.current.dockedConsoles[0].nodeId).toBe('node-2');
      expect(result.current.dockedConsoles[1].nodeId).toBe('node-1');
    });
  });

  // ── Docking and undocking ──

  describe('dock and undock', () => {
    it('undocks a console from bottom panel to floating window', () => {
      const { result } = renderConsoleHook(testNodes, dockedPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      expect(result.current.dockedConsoles).toHaveLength(1);

      act(() => {
        result.current.handleUndockConsole('node-1', 200, 150);
      });

      expect(result.current.dockedConsoles).toHaveLength(0);
      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.consoleWindows[0].deviceIds).toEqual(['node-1']);
      expect(result.current.consoleWindows[0].x).toBe(200);
      expect(result.current.consoleWindows[0].y).toBe(150);
      expect(result.current.activeBottomTabId).toBe('log'); // Reverts to log
    });

    it('docks a floating window to the bottom panel', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      const windowId = result.current.consoleWindows[0].id;

      act(() => {
        result.current.handleDockWindow(windowId, setIsTaskLogVisible);
      });

      expect(result.current.consoleWindows).toHaveLength(0);
      expect(result.current.dockedConsoles).toHaveLength(1);
      expect(result.current.dockedConsoles[0].nodeId).toBe('node-1');
      expect(setIsTaskLogVisible).toHaveBeenCalledWith(true);
    });

    it('handles docking window with invalid ID gracefully', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleDockWindow('nonexistent', setIsTaskLogVisible);
      });

      expect(result.current.dockedConsoles).toHaveLength(0);
    });
  });

  // ── Reset ──

  describe('resetConsoles', () => {
    it('clears all console state', () => {
      const { result } = renderConsoleHook(testNodes, floatingPreferences);
      const setIsTaskLogVisible = vi.fn();

      // Open a floating window
      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      // Add a docked console manually
      act(() => {
        result.current.setDockedConsoles([{ nodeId: 'node-2', nodeName: 'Router2' }]);
        result.current.setActiveBottomTabId('node-2');
      });

      act(() => {
        result.current.resetConsoles();
      });

      expect(result.current.consoleWindows).toEqual([]);
      expect(result.current.dockedConsoles).toEqual([]);
      expect(result.current.activeBottomTabId).toBe('log');
    });
  });

  // ── Default preference behavior ──

  describe('default preferences', () => {
    it('uses floating windows when preferences is null', () => {
      const { result } = renderConsoleHook(testNodes, null);
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      // Null preferences → consoleInBottomPanel defaults to false → floating
      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.dockedConsoles).toHaveLength(0);
    });

    it('uses floating windows when canvas_settings is missing', () => {
      const { result } = renderConsoleHook(testNodes, {});
      const setIsTaskLogVisible = vi.fn();

      act(() => {
        result.current.handleOpenConsole('node-1', setIsTaskLogVisible);
      });

      expect(result.current.consoleWindows).toHaveLength(1);
      expect(result.current.dockedConsoles).toHaveLength(0);
    });
  });
});
