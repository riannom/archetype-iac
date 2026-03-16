import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { apiRequest, rawApiRequest } from '../../api';
import { ScenarioStepData } from '../hooks/useLabStateWS';
import type { Node, Link } from '../types';
import ScenarioBuilder from './scenario/ScenarioBuilder';
import CreateScenarioModal from './scenario/CreateScenarioModal';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { parseScenarioYaml, serializeScenarioYaml } from './scenario/scenarioYaml';
import type { ScenarioFormState } from './scenario/scenarioTypes';

interface ScenarioSummary {
  filename: string;
  name: string;
  description: string;
  step_count: number;
}

interface ScenarioDetail {
  filename: string;
  name: string;
  description: string;
  steps: Record<string, unknown>[];
  raw_yaml: string;
}

interface ScenarioPanelProps {
  labId: string;
  scenarioSteps: ScenarioStepData[];
  activeScenarioJobId: string | null;
  onStartScenario: (filename: string) => void;
  nodes: Node[];
  links: Link[];
}

const stepStatusConfig: Record<string, { icon: string; color: string; bg: string }> = {
  running: { icon: 'fa-spinner fa-spin', color: 'text-blue-500', bg: 'bg-blue-500/10' },
  passed: { icon: 'fa-check-circle', color: 'text-green-500', bg: 'bg-green-500/10' },
  failed: { icon: 'fa-times-circle', color: 'text-red-500', bg: 'bg-red-500/10' },
  error: { icon: 'fa-exclamation-triangle', color: 'text-amber-500', bg: 'bg-amber-500/10' },
};

const stepTypeBadge: Record<string, { label: string; color: string }> = {
  verify: { label: 'VERIFY', color: 'bg-blue-500/20 text-blue-600 dark:text-blue-400' },
  link_down: { label: 'LINK DOWN', color: 'bg-red-500/20 text-red-600 dark:text-red-400' },
  link_up: { label: 'LINK UP', color: 'bg-green-500/20 text-green-600 dark:text-green-400' },
  node_stop: { label: 'STOP', color: 'bg-orange-500/20 text-orange-600 dark:text-orange-400' },
  node_start: { label: 'START', color: 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400' },
  wait: { label: 'WAIT', color: 'bg-stone-500/20 text-stone-600 dark:text-stone-400' },
  exec: { label: 'EXEC', color: 'bg-purple-500/20 text-purple-600 dark:text-purple-400' },
};

type EditorMode = 'form' | 'yaml';

const ScenarioPanel: React.FC<ScenarioPanelProps> = ({
  labId,
  scenarioSteps,
  activeScenarioJobId,
  onStartScenario,
  nodes,
  links,
}) => {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [editorContent, setEditorContent] = useState('');
  const [editorDirty, setEditorDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());
  const [forceEditor, setForceEditor] = useState(false);

  // Form builder state
  const [editorMode, setEditorMode] = useState<EditorMode>('form');
  const [formState, setFormState] = useState<ScenarioFormState | null>(null);
  const [parseWarning, setParseWarning] = useState<string | null>(null);

  // Modals
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const isRunning = !!activeScenarioJobId;
  const activeStepIndex = scenarioSteps.find(s => s.status === 'running')?.step_index ?? -1;

  const loadScenarios = useCallback(async () => {
    try {
      const data = await apiRequest<ScenarioSummary[]>(`/labs/${labId}/scenarios`);
      setScenarios(data);
    } catch {
      // ignore
    }
  }, [labId]);

  useEffect(() => {
    loadScenarios();
  }, [loadScenarios]);

  /** Try to parse YAML into form state, falling back to yaml mode. */
  const applyYamlToForm = useCallback((yamlContent: string) => {
    const result = parseScenarioYaml(yamlContent);
    if (result.ok) {
      setFormState(result.state);
      setEditorMode('form');
      setParseWarning(null);
    } else {
      setFormState(null);
      setEditorMode('yaml');
      setParseWarning(result.error);
    }
  }, []);

  const handleSelectScenario = useCallback(async (filename: string) => {
    setSelectedFile(filename);
    setEditorDirty(false);
    setForceEditor(false);
    try {
      const data = await apiRequest<ScenarioDetail>(`/labs/${labId}/scenarios/${filename}`);
      setEditorContent(data.raw_yaml);
      applyYamlToForm(data.raw_yaml);
    } catch {
      setEditorContent('# Failed to load scenario');
      setFormState(null);
      setEditorMode('yaml');
    }
  }, [labId, applyYamlToForm]);

  const handleSave = useCallback(async () => {
    if (!selectedFile) return;
    setSaving(true);
    try {
      const resp = await rawApiRequest(`/labs/${labId}/scenarios/${selectedFile}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editorContent }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => 'Save failed');
        throw new Error(detail);
      }
      setEditorDirty(false);
      loadScenarios();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to save scenario');
    } finally {
      setSaving(false);
    }
  }, [labId, selectedFile, editorContent, loadScenarios]);

  const handleCreate = useCallback(async (filename: string) => {
    const humanName = filename.replace(/\.ya?ml$/, '').replace(/[_-]/g, ' ');
    const template = `name: ${humanName}\ndescription: ""\nsteps:\n  - type: verify\n    name: Baseline check\n    specs:\n      - type: ping\n        source: node1\n        target: 10.0.0.1\n`;
    try {
      const resp = await rawApiRequest(`/labs/${labId}/scenarios/${filename}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: template }),
      });
      if (!resp.ok) throw new Error('Failed to create scenario');
      await loadScenarios();
      handleSelectScenario(filename);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to create scenario');
    }
  }, [labId, loadScenarios, handleSelectScenario]);

  const handleDelete = useCallback(async (filename: string) => {
    try {
      const resp = await rawApiRequest(`/labs/${labId}/scenarios/${filename}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error('Failed to delete scenario');
      if (selectedFile === filename) {
        setSelectedFile(null);
        setEditorContent('');
        setFormState(null);
      }
      loadScenarios();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to delete scenario');
    }
  }, [labId, selectedFile, loadScenarios]);

  const handleConfirmDelete = useCallback(() => {
    if (deleteTarget) {
      handleDelete(deleteTarget);
      setDeleteTarget(null);
    }
  }, [deleteTarget, handleDelete]);

  const toggleStepExpanded = useCallback((index: number) => {
    setExpandedSteps(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  /** Handle form state changes: serialize to YAML + mark dirty */
  const handleFormChange = useCallback((newState: ScenarioFormState) => {
    setFormState(newState);
    const yamlStr = serializeScenarioYaml(newState);
    setEditorContent(yamlStr);
    setEditorDirty(true);
  }, []);

  /** Toggle between form and yaml modes */
  const handleToggleMode = useCallback(() => {
    if (editorMode === 'form') {
      // Switch to YAML — always works since we keep editorContent in sync
      setEditorMode('yaml');
    } else {
      // Switch to form — try parsing
      const result = parseScenarioYaml(editorContent);
      if (result.ok) {
        setFormState(result.state);
        setEditorMode('form');
        setParseWarning(null);
      } else {
        setParseWarning(result.error);
      }
    }
  }, [editorMode, editorContent]);

  /** Handle raw YAML text changes */
  const handleYamlChange = useCallback((value: string) => {
    setEditorContent(value);
    setEditorDirty(true);
    // Also try to update form state silently
    const result = parseScenarioYaml(value);
    if (result.ok) {
      setFormState(result.state);
      setParseWarning(null);
    }
  }, []);

  const completionStep = scenarioSteps.find(s => s.step_index === -1);
  const overallStatus = completionStep?.status ?? null;
  const showTimeline = isRunning || (scenarioSteps.length > 0 && !forceEditor);

  useEffect(() => {
    if (isRunning) setForceEditor(false);
  }, [isRunning]);

  const totalSteps = scenarioSteps.length > 0 ? scenarioSteps[0].total_steps : 0;
  const completedCount = scenarioSteps.filter(s => s.step_index >= 0 && s.status !== 'running').length;

  // Memoize the selected scenario name for the header
  const selectedScenarioName = useMemo(
    () => scenarios.find(s => s.filename === selectedFile)?.name || selectedFile,
    [scenarios, selectedFile]
  );

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left sidebar: scenario list */}
      <div className="w-56 border-r border-stone-200 dark:border-stone-700 flex flex-col bg-white dark:bg-stone-900">
        <div className="p-3 border-b border-stone-200 dark:border-stone-700 flex items-center justify-between">
          <span className="text-[11px] font-black uppercase tracking-widest text-stone-500 dark:text-stone-400">Scenarios</span>
          <button
            onClick={() => setCreateModalOpen(true)}
            className="text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 transition-colors"
            title="Create scenario"
          >
            <i className="fa-solid fa-plus text-xs" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {scenarios.length === 0 && (
            <div className="p-4 text-xs text-stone-400 dark:text-stone-500 text-center">
              No scenarios yet
            </div>
          )}
          {scenarios.map(s => (
            <div
              key={s.filename}
              onClick={() => handleSelectScenario(s.filename)}
              className={`px-3 py-2.5 cursor-pointer border-b border-stone-100 dark:border-stone-800 transition-colors group ${
                selectedFile === s.filename
                  ? 'bg-sage-500/10 border-l-2 border-l-sage-500'
                  : 'hover:bg-stone-50 dark:hover:bg-stone-800/50 border-l-2 border-l-transparent'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-stone-700 dark:text-stone-300 truncate">{s.name}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); setDeleteTarget(s.filename); }}
                  className="opacity-0 group-hover:opacity-100 text-stone-400 hover:text-red-500 transition-all"
                  title="Delete"
                >
                  <i className="fa-solid fa-trash-can text-[11px]" />
                </button>
              </div>
              <div className="text-[11px] text-stone-400 dark:text-stone-500 mt-0.5">
                {s.step_count} step{s.step_count !== 1 ? 's' : ''}
                {s.description && ` \u2014 ${s.description}`}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header bar */}
        {selectedFile && (
          <div className="px-4 py-2.5 border-b border-stone-200 dark:border-stone-700 flex items-center justify-between bg-white dark:bg-stone-900">
            <div className="flex items-center gap-3">
              <span className="text-xs font-bold text-stone-700 dark:text-stone-300">
                {selectedScenarioName}
              </span>
              {editorDirty && (
                <span className="text-[11px] text-amber-500 font-bold">UNSAVED</span>
              )}
              {showTimeline && overallStatus && (
                <span className={`text-[11px] font-black uppercase px-2 py-0.5 rounded ${
                  overallStatus === 'passed' ? 'bg-green-500/10 text-green-600 dark:text-green-400' :
                  overallStatus === 'failed' ? 'bg-red-500/10 text-red-600 dark:text-red-400' :
                  'bg-stone-500/10 text-stone-600 dark:text-stone-400'
                }`}>
                  {overallStatus}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {/* Form/YAML toggle (only in editor, not timeline) */}
              {!showTimeline && (
                <button
                  onClick={handleToggleMode}
                  className="px-2 py-1 text-[11px] font-bold text-stone-500 hover:text-stone-700 dark:text-stone-400 dark:hover:text-stone-200 bg-stone-100 dark:bg-stone-800 rounded transition-colors"
                  title={editorMode === 'form' ? 'Switch to YAML editor' : 'Switch to form builder'}
                >
                  <i className={`fa-solid ${editorMode === 'form' ? 'fa-code' : 'fa-list'} mr-1`} />
                  {editorMode === 'form' ? 'YAML' : 'Form'}
                </button>
              )}
              {editorDirty && (
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-3 py-1 text-[11px] font-bold bg-sage-600 text-white rounded hover:bg-sage-700 transition-colors disabled:opacity-50"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              )}
              <button
                onClick={() => onStartScenario(selectedFile)}
                disabled={isRunning || editorDirty}
                className="px-3 py-1 text-[11px] font-bold bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors disabled:opacity-50 flex items-center gap-1.5"
                title={editorDirty ? 'Save before running' : isRunning ? 'Scenario in progress' : 'Execute scenario'}
              >
                <i className={`fa-solid ${isRunning ? 'fa-spinner fa-spin' : 'fa-play'} text-[11px]`} />
                {isRunning ? 'Running...' : 'Run'}
              </button>
            </div>
          </div>
        )}

        {/* Content area */}
        {!selectedFile ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-flask text-3xl mb-3 block" />
              <p className="text-sm font-bold">Select or create a scenario</p>
              <p className="text-xs mt-1">Define step-by-step network test sequences</p>
            </div>
          </div>
        ) : showTimeline ? (
          /* Timeline view */
          <div className="flex-1 overflow-y-auto p-4">
            {isRunning && totalSteps > 0 && (
              <div className="mb-4 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center gap-2">
                <i className="fa-solid fa-spinner fa-spin text-blue-500 text-xs" />
                <span className="text-xs font-bold text-blue-600 dark:text-blue-400">
                  Step {activeStepIndex >= 0 ? activeStepIndex + 1 : completedCount} of {totalSteps}
                </span>
              </div>
            )}

            <div className="space-y-1">
              {scenarioSteps
                .filter(s => s.step_index >= 0)
                .sort((a, b) => a.step_index - b.step_index)
                .map(step => {
                  const cfg = stepStatusConfig[step.status] || stepStatusConfig.error;
                  const badge = stepTypeBadge[step.step_type];
                  const isExpanded = expandedSteps.has(step.step_index);
                  const hasDetail = step.output || step.error;

                  return (
                    <div key={step.step_index} className={`rounded-lg border transition-colors ${
                      step.status === 'running'
                        ? 'border-blue-500/30 bg-blue-500/5'
                        : 'border-stone-200 dark:border-stone-700 bg-white dark:bg-stone-900'
                    }`}>
                      <div
                        className={`flex items-center gap-3 px-3 py-2.5 ${hasDetail ? 'cursor-pointer' : ''}`}
                        onClick={() => hasDetail && toggleStepExpanded(step.step_index)}
                      >
                        <i className={`fa-solid ${cfg.icon} ${cfg.color} text-sm w-4 text-center`} />
                        <span className="text-xs font-bold text-stone-700 dark:text-stone-300 flex-1 truncate">
                          {step.step_name}
                        </span>
                        {badge && (
                          <span className={`text-[11px] font-black uppercase px-1.5 py-0.5 rounded ${badge.color}`}>
                            {badge.label}
                          </span>
                        )}
                        {step.duration_ms != null && (
                          <span className="text-[11px] text-stone-400 dark:text-stone-500 font-mono tabular-nums">
                            {step.duration_ms > 1000 ? `${(step.duration_ms / 1000).toFixed(1)}s` : `${Math.round(step.duration_ms)}ms`}
                          </span>
                        )}
                        {hasDetail && (
                          <i className={`fa-solid fa-chevron-${isExpanded ? 'up' : 'down'} text-[11px] text-stone-400`} />
                        )}
                      </div>
                      {isExpanded && hasDetail && (
                        <div className="px-3 pb-2.5 pt-0">
                          {step.error && (
                            <div className="text-[11px] text-red-500 dark:text-red-400 font-mono bg-red-500/5 rounded px-2 py-1 mb-1">
                              {step.error}
                            </div>
                          )}
                          {step.output && (
                            <pre className="text-[11px] text-stone-600 dark:text-stone-400 font-mono bg-stone-100 dark:bg-stone-800 rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap max-h-40 overflow-y-auto">
                              {step.output}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>

            {!isRunning && (
              <button
                onClick={() => setForceEditor(true)}
                className="mt-4 text-[11px] text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
              >
                <i className="fa-solid fa-pen-to-square mr-1" />
                Edit scenario
              </button>
            )}
          </div>
        ) : editorMode === 'form' && formState ? (
          /* Form builder view */
          <ScenarioBuilder
            state={formState}
            onChange={handleFormChange}
            nodes={nodes}
            links={links}
            disabled={isRunning}
          />
        ) : (
          /* YAML editor view */
          <div className="flex-1 flex flex-col overflow-hidden">
            {parseWarning && (
              <div className="px-4 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-2">
                <i className="fa-solid fa-triangle-exclamation text-amber-500 text-xs" />
                <span className="text-[11px] text-amber-600 dark:text-amber-400">
                  Cannot parse as form: {parseWarning}
                </span>
              </div>
            )}
            <textarea
              value={editorContent}
              onChange={(e) => handleYamlChange(e.target.value)}
              className="flex-1 p-4 font-mono text-xs bg-white dark:bg-stone-950 text-stone-800 dark:text-stone-200 resize-none outline-none border-none"
              spellCheck={false}
              placeholder="# Write your scenario YAML here..."
            />
          </div>
        )}
      </div>

      {/* Modals */}
      <CreateScenarioModal
        isOpen={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCreate={handleCreate}
      />
      <ConfirmDialog
        isOpen={!!deleteTarget}
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Delete Scenario"
        message={`Are you sure you want to delete "${deleteTarget}"? This action cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
      />
    </div>
  );
};

export default ScenarioPanel;
