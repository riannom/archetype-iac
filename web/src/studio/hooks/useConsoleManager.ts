import { useCallback, useState } from 'react';
import { ConsoleWindow, Node } from '../types';
import { DockedConsole } from '../components/TaskLogPanel';

interface UseConsoleManagerOptions {
  nodes: Node[];
  preferences: { canvas_settings?: { consoleInBottomPanel?: boolean } } | null;
}

export function useConsoleManager({ nodes, preferences }: UseConsoleManagerOptions) {
  const [consoleWindows, setConsoleWindows] = useState<ConsoleWindow[]>([]);
  const [dockedConsoles, setDockedConsoles] = useState<DockedConsole[]>([]);
  const [activeBottomTabId, setActiveBottomTabId] = useState<string>('log');

  const handleOpenConsole = useCallback((nodeId: string, setIsTaskLogVisible: (v: boolean) => void) => {
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;

    const openInPanel = preferences?.canvas_settings?.consoleInBottomPanel ?? false;

    if (openInPanel) {
      // Check if already docked
      if (dockedConsoles.some((c) => c.nodeId === nodeId)) {
        setActiveBottomTabId(nodeId);
        setIsTaskLogVisible(true);
        return;
      }
      // Add to docked consoles
      setDockedConsoles((prev) => [...prev, { nodeId, nodeName: node.name }]);
      setActiveBottomTabId(nodeId);
      setIsTaskLogVisible(true);
    } else {
      // Existing floating window logic
      setConsoleWindows((prev) => {
        const existingWin = prev.find((win) => win.deviceIds.includes(nodeId));
        if (existingWin) {
          return prev.map((win) => (win.id === existingWin.id ? { ...win, activeDeviceId: nodeId } : win));
        }
        const newWin: ConsoleWindow = {
          id: Math.random().toString(36).slice(2, 9),
          deviceIds: [nodeId],
          activeDeviceId: nodeId,
          x: 100,
          y: 100,
          isExpanded: true,
        };
        return [...prev, newWin];
      });
    }
  }, [nodes, preferences, dockedConsoles]);

  // Merge all tabs from source window into target window
  const handleMergeWindows = useCallback((sourceWindowId: string, targetWindowId: string) => {
    setConsoleWindows((prev) => {
      const sourceWin = prev.find((w) => w.id === sourceWindowId);
      const targetWin = prev.find((w) => w.id === targetWindowId);
      if (!sourceWin || !targetWin) return prev;

      // Merge deviceIds, using Set to deduplicate
      const mergedDeviceIds = [...new Set([...targetWin.deviceIds, ...sourceWin.deviceIds])];

      return prev
        .filter((w) => w.id !== sourceWindowId) // Remove source window
        .map((w) =>
          w.id === targetWindowId
            ? { ...w, deviceIds: mergedDeviceIds }
            : w
        );
    });
  }, []);

  // Split a tab out of a window into a new separate window
  const handleSplitTab = useCallback((windowId: string, deviceId: string, x: number, y: number) => {
    setConsoleWindows((prev) => {
      const sourceWin = prev.find((w) => w.id === windowId);
      if (!sourceWin || sourceWin.deviceIds.length <= 1) return prev;

      // Create new window for the split tab
      const newWin: ConsoleWindow = {
        id: Math.random().toString(36).slice(2, 9),
        deviceIds: [deviceId],
        activeDeviceId: deviceId,
        x: Math.max(0, x),
        y: Math.max(0, y),
        isExpanded: true,
      };

      // Remove tab from source window
      const updatedWindows = prev.map((w) => {
        if (w.id !== windowId) return w;
        const newDeviceIds = w.deviceIds.filter((id) => id !== deviceId);
        return {
          ...w,
          deviceIds: newDeviceIds,
          activeDeviceId: w.activeDeviceId === deviceId ? newDeviceIds[0] : w.activeDeviceId,
        };
      });

      return [...updatedWindows, newWin];
    });
  }, []);

  // Reorder tabs within a console window
  const handleReorderTab = useCallback((windowId: string, fromIndex: number, toIndex: number) => {
    setConsoleWindows((prev) =>
      prev.map((win) => {
        if (win.id !== windowId) return win;
        const newDeviceIds = [...win.deviceIds];
        const [movedId] = newDeviceIds.splice(fromIndex, 1);
        // Adjust toIndex if moving right (since we removed an element)
        const adjustedTo = toIndex > fromIndex ? toIndex - 1 : toIndex;
        newDeviceIds.splice(adjustedTo, 0, movedId);
        return { ...win, deviceIds: newDeviceIds };
      })
    );
  }, []);

  // Toggle minimize state of a console window
  const handleToggleMinimize = useCallback((windowId: string) => {
    setConsoleWindows((prev) =>
      prev.map((win) =>
        win.id === windowId ? { ...win, isExpanded: !win.isExpanded } : win
      )
    );
  }, []);

  // Undock a console from the bottom panel to a floating window
  const handleUndockConsole = useCallback((nodeId: string, x: number, y: number) => {
    // Remove from docked
    setDockedConsoles((prev) => prev.filter((c) => c.nodeId !== nodeId));
    if (activeBottomTabId === nodeId) {
      setActiveBottomTabId('log');
    }

    // Add to floating windows
    const newWin: ConsoleWindow = {
      id: Math.random().toString(36).slice(2, 9),
      deviceIds: [nodeId],
      activeDeviceId: nodeId,
      x: Math.max(0, x),
      y: Math.max(0, y),
      isExpanded: true,
    };
    setConsoleWindows((prev) => [...prev, newWin]);
  }, [activeBottomTabId]);

  // Dock a floating window to the bottom panel
  const handleDockWindow = useCallback((windowId: string, setIsTaskLogVisible: (v: boolean) => void) => {
    const win = consoleWindows.find((w) => w.id === windowId);
    if (!win) return;

    // Add all tabs to docked
    win.deviceIds.forEach((nodeId) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      if (dockedConsoles.some((c) => c.nodeId === nodeId)) return;
      setDockedConsoles((prev) => [...prev, { nodeId, nodeName: node.name }]);
    });

    setActiveBottomTabId(win.activeDeviceId);
    setIsTaskLogVisible(true);

    // Remove floating window
    setConsoleWindows((prev) => prev.filter((w) => w.id !== windowId));
  }, [consoleWindows, nodes, dockedConsoles]);

  // Close a floating console window
  const handleCloseConsoleWindow = useCallback((id: string) => {
    setConsoleWindows((prev) => prev.filter((win) => win.id !== id));
  }, []);

  // Close a tab within a console window
  const handleCloseConsoleTab = useCallback((winId: string, nodeId: string) => {
    setConsoleWindows((prev) =>
      prev
        .map((win) => {
          if (win.id !== winId) return win;
          const nextIds = win.deviceIds.filter((did) => did !== nodeId);
          const nextActive = win.activeDeviceId === nodeId ? nextIds[0] || '' : win.activeDeviceId;
          return { ...win, deviceIds: nextIds, activeDeviceId: nextActive };
        })
        .filter((win) => win.deviceIds.length > 0)
    );
  }, []);

  // Set active tab in a console window
  const handleSetActiveConsoleTab = useCallback((winId: string, nodeId: string) => {
    setConsoleWindows((prev) => prev.map((win) => (win.id === winId ? { ...win, activeDeviceId: nodeId } : win)));
  }, []);

  // Update console window position
  const handleUpdateConsoleWindowPos = useCallback((id: string, x: number, y: number) => {
    setConsoleWindows((prev) => prev.map((win) => (win.id === id ? { ...win, x, y } : win)));
  }, []);

  // Close a docked console tab
  const handleCloseDockedConsole = useCallback((nodeId: string) => {
    setDockedConsoles((prev) => prev.filter((c) => c.nodeId !== nodeId));
    if (activeBottomTabId === nodeId) {
      setActiveBottomTabId('log');
    }
  }, [activeBottomTabId]);

  // Reorder docked console tabs
  const handleReorderDockedTab = useCallback((fromIndex: number, toIndex: number) => {
    setDockedConsoles((prev) => {
      const newTabs = [...prev];
      const [movedTab] = newTabs.splice(fromIndex, 1);
      // Adjust toIndex if moving right (since we removed an element)
      const adjustedTo = toIndex > fromIndex ? toIndex - 1 : toIndex;
      newTabs.splice(adjustedTo, 0, movedTab);
      return newTabs;
    });
  }, []);

  // Reset all console state (for lab switches)
  const resetConsoles = useCallback(() => {
    setConsoleWindows([]);
    setDockedConsoles([]);
    setActiveBottomTabId('log');
  }, []);

  return {
    consoleWindows,
    setConsoleWindows,
    dockedConsoles,
    setDockedConsoles,
    activeBottomTabId,
    setActiveBottomTabId,
    handleOpenConsole,
    handleMergeWindows,
    handleSplitTab,
    handleReorderTab,
    handleToggleMinimize,
    handleUndockConsole,
    handleDockWindow,
    handleCloseConsoleWindow,
    handleCloseConsoleTab,
    handleSetActiveConsoleTab,
    handleUpdateConsoleWindowPos,
    handleCloseDockedConsole,
    handleReorderDockedTab,
    resetConsoles,
  };
}
