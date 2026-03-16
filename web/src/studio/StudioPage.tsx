import React, { useCallback, useMemo, useState } from 'react';
import Sidebar from './components/Sidebar';
import Canvas from './components/Canvas';
import TopBar from './components/TopBar';
import PropertiesPanel from './components/PropertiesPanel';
import ConsoleManager from './components/ConsoleManager';
import RuntimeControl from './components/RuntimeControl';
import TaskLogPanel from './components/TaskLogPanel';
import Auth from './components/Auth';
import Dashboard from './components/Dashboard';
import SystemStatusStrip from './components/SystemStatusStrip';
import AgentAlertBanner from './components/AgentAlertBanner';
import ConfigViewerModal from './components/ConfigViewerModal';
import JobLogModal from './components/JobLogModal';
import TaskLogEntryModal from './components/TaskLogEntryModal';
import ConfigsView from './components/ConfigsView';
import LogsView from './components/LogsView';
import VerificationPanel from './components/VerificationPanel';
import ScenarioPanel from './components/ScenarioPanel';
import InfraView from './components/InfraView';
import ViewTabBar from './components/ViewTabBar';
import type { LabView } from './components/ViewTabBar';
import { usePortManager } from './hooks/usePortManager';
import { useLabStateWS } from './hooks/useLabStateWS';
import { useTheme } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { canViewInfrastructure } from '../utils/permissions';
import { useNotifications } from '../contexts/NotificationContext';
import { useImageLibrary } from '../contexts/ImageLibraryContext';
import { useDeviceCatalog } from '../contexts/DeviceCatalogContext';
import { useLabTopology } from './hooks/useLabTopology';
import { useNodeStates } from './hooks/useNodeStates';
import { useConsoleManager } from './hooks/useConsoleManager';
import { useJobTracking } from './hooks/useJobTracking';
import { useLabDataLoading, LabSummary } from './hooks/useLabDataLoading';
import { useStudioModals } from './hooks/useStudioModals';
import { useStudioAuth } from './hooks/useStudioAuth';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useTopologyHandlers } from './hooks/useTopologyHandlers';
import { useLabLifecycle } from './hooks/useLabLifecycle';
import './studio.css';
import 'xterm/css/xterm.css';

const StudioPage: React.FC = () => {
  const { effectiveMode } = useTheme();
  const { user, refreshUser, clearUser } = useUser();
  const { addNotification, preferences } = useNotifications();
  const { imageLibrary } = useImageLibrary();
  const { deviceModels, deviceCategories, refresh: refreshDeviceCatalog } = useDeviceCatalog();
  const showAdminStrip = canViewInfrastructure(user ?? null);
  const [activeLab, setActiveLab] = useState<LabSummary | null>(null);
  const [view, setView] = useState<LabView>('designer');
  const isDesignerView = view === 'designer';
  const [showAgentIndicators, setShowAgentIndicators] = useState<boolean>(() => {
    return localStorage.getItem('archetype_show_agent_indicators') !== 'false';
  });

  // --- Extracted hooks ---

  const {
    authRequired, authError, authLoading,
    studioRequest, handleLogin: attemptLogin, beginLogout,
  } = useStudioAuth();

  const {
    selectedId, setSelectedId, selectedIds, activeTool, setActiveTool,
    focusNodeId, setFocusNodeId,
    sidebarCollapsed, setSidebarCollapsed, sidebarTab, setSidebarTab,
    handleSelectTool, handleCanvasSelect, handleSelectMultiple, clearSelection,
  } = useCanvasInteraction();

  const {
    configViewerOpen, configViewerNode, configViewerSnapshot,
    handleOpenConfigViewer, handleCloseConfigViewer,
    jobLogModalOpen, jobLogModalJobId, handleCloseJobLogModal,
    taskLogEntryModalOpen, taskLogEntryModalEntry, handleCloseTaskLogEntryModal,
    handleTaskLogEntryClick,
    showYamlModal, yamlContent, openYamlPreview, closeYamlPreview,
  } = useStudioModals();

  // --- Hook compositions ---

  const {
    labs, setLabs, agents, labStatuses, systemMetrics,
    loadLabs,
  } = useLabDataLoading({ studioRequest, activeLab });

  const {
    loadJobs,
    addTaskLogEntry, clearTaskLog, filteredTaskLog,
    isTaskLogVisible, setIsTaskLogVisible,
    taskLogAutoRefresh, setTaskLogAutoRefresh,
    handleWSJobProgress, handleWSTestResult, handleWSScenarioStep,
    testResults, setTestResults, testSummary, setTestSummary,
    testRunning, setTestRunning,
    scenarioSteps, activeScenarioJobId, setActiveScenarioJobId,
    resetJobTracking,
  } = useJobTracking({ studioRequest, addNotification });

  const {
    nodes, setNodes, links, setLinks, annotations, setAnnotations,
    nodesRef, linksRef, layoutDirtyRef,
    saveLayout, triggerLayoutSave, triggerTopologySave, flushTopologySave,
  } = useLabTopology({
    activeLab,
    deviceModels,
    studioRequest,
    addTaskLogEntry,
  });

  const {
    nodeStates, setNodeStates, runtimeStates,
    pendingNodeOps, setPendingNodeOps,
    optimisticGuardRef,
    handleWSNodeStateChange,
    loadNodeStates, loadNodeReadiness, refreshNodeStatesFromAgent,
  } = useNodeStates({
    activeLabId: activeLab?.id || null,
    studioRequest,
    addNotification,
  });

  const consoleManager = useConsoleManager({
    nodes,
    preferences,
  });

  const portManager = usePortManager(nodes, links);

  // WebSocket hook for real-time state updates
  const {
    isConnected: wsConnected,
    reconnectAttempts: wsReconnectAttempts,
    linkStates,
  } = useLabStateWS(activeLab?.id || null, {
    onNodeStateChange: handleWSNodeStateChange,
    onJobProgress: handleWSJobProgress,
    onTestResult: handleWSTestResult,
    onScenarioStep: handleWSScenarioStep,
    enabled: !!activeLab,
  });

  // --- Lab lifecycle (create, select, exit, delete, rename, login, logout, polling) ---

  const {
    handleCreateLab, handleSelectLab, handleExitLab, handleLogout,
    handleDeleteLab, handleRenameLab, handleDownloadBundle, handleLogin,
  } = useLabLifecycle({
    activeLab, setActiveLab, labs, setLabs,
    nodes, annotations, setNodes, setLinks, setAnnotations,
    setView: setView as React.Dispatch<React.SetStateAction<string>>,
    studioRequest, loadLabs,
    layoutDirtyRef, saveLayout,
    resetJobTracking, clearSelection, consoleManager,
    beginLogout, clearUser, attemptLogin, refreshUser, refreshDeviceCatalog,
    addNotification,
    wsConnected, refreshNodeStatesFromAgent, loadNodeStates, loadJobs, loadNodeReadiness,
  });

  // --- Topology & action handlers ---

  const {
    handleAddDevice, handleAddExternalNetwork, handleCanvasToolCreate,
    handleNodeMove, handleAnnotationMove, handleConnect,
    handleUpdateNode, handleUpdateLink, handleUpdateAnnotation, handleDelete,
    handleUpdateStatus, handleOpenConsole, handleDockWindow,
    handleExtractNodeConfig, handleStartTests, handleStartScenario,
    handleExtractConfigs,
  } = useTopologyHandlers({
    activeLab, nodes, links, annotations, deviceModels, imageLibrary, effectiveMode,
    runtimeStates,
    setNodes, setLinks, setAnnotations, setSelectedId, setActiveTool, clearSelection,
    nodesRef, linksRef,
    triggerLayoutSave, triggerTopologySave, flushTopologySave,
    studioRequest, addTaskLogEntry, addNotification,
    portManager,
    pendingNodeOps, setPendingNodeOps, setNodeStates, optimisticGuardRef,
    loadJobs,
    setIsTaskLogVisible, consoleManager,
    testRunning, setTestResults, setTestSummary, setTestRunning,
    activeScenarioJobId, setActiveScenarioJobId,
  });

  // Derive canvas highlights from the currently-running scenario step
  const activeScenarioHighlights = useMemo(() => {
    if (!activeScenarioJobId) return undefined;
    const runningStep = scenarioSteps.find(s => s.status === 'running' && s.step_index >= 0);
    if (!runningStep || !runningStep.step_data) return undefined;

    const activeNodeNames = new Set<string>();
    let activeLinkName: string | null = null;
    const stepType = runningStep.step_type;
    const sd = runningStep.step_data;

    if (stepType === 'link_down' || stepType === 'link_up') {
      const link = (sd.link as string) || '';
      activeLinkName = link;
      const parts = link.split(' <-> ');
      parts.forEach(p => {
        const nodeName = p.trim().split(':')[0];
        if (nodeName) activeNodeNames.add(nodeName);
      });
    } else if (stepType === 'node_stop' || stepType === 'node_start' || stepType === 'exec') {
      const node = (sd.node as string) || '';
      if (node) activeNodeNames.add(node);
    } else if (stepType === 'verify') {
      const specs = (sd.specs as Array<Record<string, unknown>>) || [];
      specs.forEach(spec => {
        if (spec.source) activeNodeNames.add(spec.source as string);
        if (spec.node) activeNodeNames.add(spec.node as string);
        if (spec.node_name) activeNodeNames.add(spec.node_name as string);
      });
    }

    if (activeNodeNames.size === 0 && !activeLinkName) return undefined;
    return { activeNodeNames, activeLinkName, stepName: runningStep.step_name };
  }, [activeScenarioJobId, scenarioSteps]);

  const handleToggleAgentIndicators = useCallback(() => {
    setShowAgentIndicators(prev => {
      const next = !prev;
      localStorage.setItem('archetype_show_agent_indicators', next ? 'true' : 'false');
      return next;
    });
  }, []);

  const handleExport = useCallback(async () => {
    if (!activeLab) return;
    const data = await studioRequest<{ content: string }>(`/labs/${activeLab.id}/export-yaml`);
    openYamlPreview(data.content || '');
  }, [activeLab, studioRequest, openYamlPreview]);

  const handleExportFull = useCallback(async () => {
    if (!activeLab) return;
    await saveLayout(activeLab.id, nodes, annotations);
    await handleDownloadBundle(activeLab);
  }, [activeLab, saveLayout, nodes, annotations, handleDownloadBundle]);

  const selectedItem = nodes.find((node) => node.id === selectedId)
    || links.find((link) => link.id === selectedId)
    || annotations.find((ann) => ann.id === selectedId)
    || null;

  // --- Render helpers ---

  const renderView = () => {
    switch (view) {
      case 'configs':
        return (
          <ConfigsView
            labId={activeLab?.id || ''}
            nodes={nodes}
            runtimeStates={runtimeStates}
            studioRequest={studioRequest}
            onExtractConfigs={handleExtractConfigs}
          />
        );
      case 'logs':
        return (
          <LogsView
            labId={activeLab?.id || ''}
            studioRequest={studioRequest}
            realtimeEntries={filteredTaskLog}
          />
        );
      case 'runtime':
        return (
          <RuntimeControl
            labId={activeLab?.id || ''}
            nodes={nodes}
            runtimeStates={runtimeStates}
            nodeStates={nodeStates}
            deviceModels={deviceModels}
            onUpdateStatus={handleUpdateStatus}
            onSetRuntimeStatus={(nodeId, status) => {
              const actualStateMap: Record<string, string> = {
                booting: 'starting', stopping: 'stopping', running: 'running', stopped: 'stopped', error: 'error',
              };
              const actualState = actualStateMap[status] || status;
              if (actualState === 'stopping' || actualState === 'starting') {
                optimisticGuardRef.current.set(nodeId, Date.now() + 5000);
              }
              setNodeStates(prev => ({
                ...prev,
                [nodeId]: {
                  ...prev[nodeId],
                  actual_state: actualState,
                  desired_state: status === 'stopping' ? 'stopped' : status === 'booting' ? 'running' : prev[nodeId]?.desired_state,
                  display_state: actualState === 'stopping' ? 'stopping' : actualState === 'starting' ? 'starting' : undefined,
                },
              }));
            }}
            onRefreshStates={async () => {
              if (activeLab) {
                await refreshNodeStatesFromAgent(activeLab.id);
                await loadNodeStates(activeLab.id, nodes);
              }
            }}
            studioRequest={studioRequest}
            agents={agents}
            onUpdateNode={handleUpdateNode}
            pendingNodeOps={pendingNodeOps}
            onFlushTopologySave={flushTopologySave}
          />
        );
      case 'tests':
        return (
          <VerificationPanel
            labId={activeLab?.id || ''}
            testResults={testResults}
            testSummary={testSummary}
            isRunning={testRunning}
            onStartTests={handleStartTests}
            nodes={nodes}
            links={links}
          />
        );
      case 'scenarios':
        return (
          <ScenarioPanel
            labId={activeLab?.id || ''}
            scenarioSteps={scenarioSteps}
            activeScenarioJobId={activeScenarioJobId}
            onStartScenario={handleStartScenario}
            nodes={nodes}
            links={links}
          />
        );
      case 'infra':
        return (
          <InfraView
            labId={activeLab?.id || ''}
            nodes={nodes}
            nodeStates={nodeStates}
            linkStates={linkStates}
            agents={agents}
            deviceModels={deviceModels}
          />
        );
      default:
        return (
          <>
            <div className={`shrink-0 transition-all duration-300 ease-in-out overflow-hidden ${sidebarCollapsed ? 'w-0' : 'w-64'}`}>
              <Sidebar
                categories={deviceCategories}
                onAddDevice={handleAddDevice}
                onSelectTool={handleSelectTool}
                activeTool={activeTool}
                onAddExternalNetwork={handleAddExternalNetwork}
                imageLibrary={imageLibrary}
                activeTab={sidebarTab}
                onTabChange={setSidebarTab}
                nodes={nodes}
                runtimeStates={runtimeStates}
                deviceModels={deviceModels}
                selectedId={selectedId}
                onFocusNode={(id) => { setFocusNodeId(id); }}
                onOpenConsole={handleOpenConsole}
                onSelectNode={(id) => { handleCanvasSelect(id); }}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() => setSidebarCollapsed(c => !c)}
              />
            </div>
            {sidebarCollapsed && (
              <button
                onClick={() => setSidebarCollapsed(false)}
                className="absolute left-2 top-2 z-20 w-8 h-8 rounded-lg bg-white/80 dark:bg-stone-800/80 backdrop-blur border border-stone-200 dark:border-stone-700 flex items-center justify-center text-stone-500 hover:text-stone-700 dark:hover:text-stone-200 shadow-sm transition-colors"
                title="Expand sidebar"
              >
                <i className="fa-solid fa-chevron-right text-[11px]" />
              </button>
            )}
            <Canvas
              nodes={nodes}
              links={links}
              annotations={annotations}
              runtimeStates={runtimeStates}
              nodeStates={nodeStates}
              linkStates={linkStates}
              scenarioHighlights={activeScenarioHighlights}
              deviceModels={deviceModels}
              labId={activeLab?.id}
              agents={agents}
              showAgentIndicators={showAgentIndicators}
              onToggleAgentIndicators={handleToggleAgentIndicators}
              activeTool={activeTool}
              onToolCreate={handleCanvasToolCreate}
              onNodeMove={handleNodeMove}
              onAnnotationMove={handleAnnotationMove}
              onConnect={handleConnect}
              selectedId={selectedId}
              onSelect={handleCanvasSelect}
              onOpenConsole={handleOpenConsole}
              onExtractConfig={handleExtractNodeConfig}
              onUpdateStatus={handleUpdateStatus}
              onDelete={handleDelete}
              onDropDevice={handleAddDevice}
              onDropExternalNetwork={handleAddExternalNetwork}
              onUpdateAnnotation={handleUpdateAnnotation}
              selectedIds={selectedIds}
              onSelectMultiple={handleSelectMultiple}
              focusNodeId={focusNodeId}
              onFocusHandled={() => setFocusNodeId(null)}
            />
            <div
              className={`shrink-0 transition-all duration-300 ease-in-out overflow-hidden ${
                selectedItem ? 'w-80' : 'w-0'
              }`}
            >
              <div className="w-80 h-full">
                <PropertiesPanel
                  selectedItem={selectedItem}
                  onUpdateNode={handleUpdateNode}
                  onUpdateLink={handleUpdateLink}
                  onUpdateAnnotation={handleUpdateAnnotation}
                  onDelete={handleDelete}
                  nodes={nodes}
                  links={links}
                  annotations={annotations}
                  onOpenConsole={handleOpenConsole}
                  runtimeStates={runtimeStates}
                  deviceModels={deviceModels}
                  onUpdateStatus={handleUpdateStatus}
                  portManager={portManager}
              onOpenConfigViewer={handleOpenConfigViewer}
              labId={activeLab?.id || ''}
              studioRequest={studioRequest}
              agents={agents}
              nodeStates={nodeStates}
            />
              </div>
            </div>
          </>
        );
    }
  };

  const backgroundGradient =
    effectiveMode === 'dark'
      ? 'bg-gradient-to-br from-stone-950/20 via-stone-900/12 to-stone-950/20 bg-gradient-animate'
      : 'bg-gradient-to-br from-stone-50/20 via-white/15 to-stone-100/20 bg-gradient-animate';

  if (authRequired) {
    return <Auth onLogin={handleLogin} error={authError} loading={authLoading} />;
  }

  if (!activeLab) {
    return (
      <Dashboard
        labs={labs}
        labStatuses={labStatuses}
        systemMetrics={systemMetrics}
        onSelect={handleSelectLab}
        onDownload={handleDownloadBundle}
        onCreate={handleCreateLab}
        onDelete={handleDeleteLab}
        onRename={handleRenameLab}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <div className={`flex flex-col h-screen overflow-hidden select-none transition-colors duration-500 ${view === 'designer' ? '' : backgroundGradient}`}>
      <TopBar labName={activeLab.name} onExport={handleExport} onExportFull={handleExportFull} onExit={handleExitLab} onRename={(newName) => handleRenameLab(activeLab.id, newName)} />
      <ViewTabBar view={view} onViewChange={setView} showInfraTab={agents.length > 1} />
      {showAdminStrip && <SystemStatusStrip metrics={systemMetrics} />}
      <AgentAlertBanner />
      <div className="flex flex-1 overflow-hidden relative">
        {renderView()}
        <div className={isDesignerView ? '' : 'hidden'} aria-hidden={!isDesignerView}>
          <ConsoleManager
            labId={activeLab.id}
            windows={consoleManager.consoleWindows}
            nodes={nodes}
            nodeStates={nodeStates}
            isVisible={isDesignerView}
            onCloseWindow={consoleManager.handleCloseConsoleWindow}
            onCloseTab={consoleManager.handleCloseConsoleTab}
            onSetActiveTab={consoleManager.handleSetActiveConsoleTab}
            onUpdateWindowPos={consoleManager.handleUpdateConsoleWindowPos}
            onMergeWindows={consoleManager.handleMergeWindows}
            onSplitTab={consoleManager.handleSplitTab}
            onReorderTab={consoleManager.handleReorderTab}
            onToggleMinimize={consoleManager.handleToggleMinimize}
            onDockWindow={handleDockWindow}
          />
        </div>
      </div>
      <TaskLogPanel
        entries={filteredTaskLog}
        isVisible={isTaskLogVisible}
        onToggle={() => setIsTaskLogVisible(!isTaskLogVisible)}
        onClear={clearTaskLog}
        autoUpdateEnabled={taskLogAutoRefresh}
        onToggleAutoUpdate={setTaskLogAutoRefresh}
        onEntryClick={handleTaskLogEntryClick}
        showConsoles={isDesignerView}
        consoleTabs={consoleManager.dockedConsoles}
        activeTabId={consoleManager.activeBottomTabId}
        onSelectTab={consoleManager.setActiveBottomTabId}
        onCloseConsoleTab={consoleManager.handleCloseDockedConsole}
        onUndockConsole={consoleManager.handleUndockConsole}
        onReorderTab={consoleManager.handleReorderDockedTab}
        labId={activeLab?.id}
        nodeStates={nodeStates}
        wsConnected={wsConnected}
        reconnectAttempts={wsReconnectAttempts}
      />
      {showYamlModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-md">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-2xl w-[700px] max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
            <div className="p-5 border-b border-stone-100 dark:border-stone-800 flex justify-between items-center">
              <h3 className="text-stone-900 dark:text-stone-100 font-bold text-sm uppercase">YAML Preview</h3>
              <button onClick={closeYamlPreview} className="text-stone-500 hover:text-stone-900 dark:hover:text-white">
                <i className="fa-solid fa-times"></i>
              </button>
            </div>
            <div className="flex-1 p-6 overflow-y-auto bg-stone-50 dark:bg-stone-950/50 font-mono text-[11px] text-sage-700 dark:text-sage-300 whitespace-pre">
              {yamlContent}
            </div>
            <div className="p-5 border-t border-stone-100 dark:border-stone-800 flex justify-end gap-3">
              <button onClick={closeYamlPreview} className="px-6 py-2 bg-sage-600 text-white font-black rounded-lg">
                DONE
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfigViewerModal
        isOpen={configViewerOpen}
        onClose={handleCloseConfigViewer}
        labId={activeLab?.id || ''}
        nodeId={configViewerNode?.id}
        nodeName={configViewerNode?.name}
        studioRequest={studioRequest}
        snapshotContent={configViewerSnapshot?.content}
        snapshotLabel={configViewerSnapshot?.label}
      />
      <JobLogModal
        isOpen={jobLogModalOpen}
        onClose={handleCloseJobLogModal}
        labId={activeLab?.id || ''}
        jobId={jobLogModalJobId || ''}
        studioRequest={studioRequest}
      />
      <TaskLogEntryModal
        isOpen={taskLogEntryModalOpen}
        onClose={handleCloseTaskLogEntryModal}
        entry={taskLogEntryModalEntry}
      />
    </div>
  );
};

export default StudioPage;
