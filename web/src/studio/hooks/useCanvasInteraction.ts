import { useState, useCallback, useEffect } from 'react';
import { CanvasTool } from '../types';
import { SidebarTab } from '../components/Sidebar';

export function useCanvasInteraction() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [activeTool, setActiveTool] = useState<CanvasTool>('pointer');
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('library');

  // ESC key returns to pointer tool
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && activeTool !== 'pointer') {
        setActiveTool('pointer');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTool]);

  const handleSelectTool = useCallback((tool: CanvasTool) => {
    setActiveTool(tool);
    if (tool !== 'pointer') {
      setSelectedId(null);
    }
  }, []);

  const handleCanvasSelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setSelectedIds(new Set());
  }, []);

  const handleSelectMultiple = useCallback((ids: Set<string>) => {
    setSelectedIds(ids);
    setSelectedId(null);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedId(null);
    setSelectedIds(new Set());
  }, []);

  return {
    selectedId,
    setSelectedId,
    selectedIds,
    activeTool,
    setActiveTool,
    focusNodeId,
    setFocusNodeId,
    sidebarCollapsed,
    setSidebarCollapsed,
    sidebarTab,
    setSidebarTab,

    handleSelectTool,
    handleCanvasSelect,
    handleSelectMultiple,
    clearSelection,
  };
}
