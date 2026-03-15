import React, { useCallback, useMemo, useState } from 'react';
import { TestResult, TestSpec, Node, Link } from '../types';
import { apiRequest } from '../../api';
import { usePersistedState } from '../hooks/usePersistedState';
import TestBuilder from './TestBuilder';

interface VerificationPanelProps {
  labId: string;
  testResults: TestResult[];
  testSummary: { total: number; passed: number; failed: number; errors: number } | null;
  isRunning: boolean;
  onStartTests: (specs?: TestSpec[]) => void;
  nodes: Node[];
  links: Link[];
}

const statusIcon: Record<string, { icon: string; color: string; label: string }> = {
  passed: { icon: 'fa-check-circle', color: 'text-green-500', label: 'PASS' },
  failed: { icon: 'fa-times-circle', color: 'text-red-500', label: 'FAIL' },
  error: { icon: 'fa-exclamation-triangle', color: 'text-amber-500', label: 'ERROR' },
  skipped: { icon: 'fa-minus-circle', color: 'text-stone-400', label: 'SKIP' },
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

const VerificationPanel: React.FC<VerificationPanelProps> = ({
  labId,
  testResults,
  testSummary,
  isRunning,
  onStartTests,
  nodes,
  links,
}) => {
  const [testSpecs, setTestSpecs] = usePersistedState<TestSpec[]>(`testSpecs-${labId}`, []);
  const [importing, setImporting] = useState(false);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  const handleImportFromYaml = useCallback(async () => {
    if (!labId) return;
    setImporting(true);
    try {
      const data = await apiRequest<{ tests: TestSpec[] }>(`/labs/${labId}/tests`);
      if (data.tests && data.tests.length > 0) {
        setTestSpecs(data.tests);
      }
    } catch {
      // Silently ignore - no tests in YAML
    } finally {
      setImporting(false);
    }
  }, [labId, setTestSpecs]);

  const handleRun = useCallback(() => {
    setExpandedRows(new Set());
    if (testSpecs.length > 0) {
      onStartTests(testSpecs);
    } else {
      onStartTests();
    }
  }, [testSpecs, onStartTests]);

  const toggleRow = useCallback((index: number) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  const totalDuration = useMemo(
    () => testResults.reduce((sum, r) => sum + r.duration_ms, 0),
    [testResults],
  );

  const handleDownload = useCallback(() => {
    if (testResults.length === 0) return;
    const report = {
      lab_id: labId,
      timestamp: new Date().toISOString(),
      summary: testSummary,
      total_duration_ms: Math.round(totalDuration),
      results: testResults,
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `test-results-${labId.slice(0, 8)}-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [labId, testResults, testSummary, totalDuration]);

  const specCount = testSpecs.length;
  const hasResults = testResults.length > 0;
  const isComplete = hasResults && !isRunning;

  return (
    <div className="h-full w-full flex flex-col bg-transparent border-t border-stone-200 dark:border-stone-700">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-200 dark:border-stone-700 flex-shrink-0 glass-surface">
        <div className="flex items-center gap-2">
          <i className="fa-solid fa-flask text-sage-600 dark:text-sage-400" />
          <span className="text-sm font-semibold text-stone-700 dark:text-stone-200">Lab Verification</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleImportFromYaml}
            disabled={isRunning || importing}
            className="px-2.5 py-1.5 rounded-lg text-xs font-medium text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {importing ? (
              <><i className="fa-solid fa-spinner fa-spin mr-1" />Importing...</>
            ) : (
              <><i className="fa-solid fa-file-import mr-1" />Import from YAML</>
            )}
          </button>
          <button
            onClick={handleRun}
            disabled={isRunning}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              isRunning
                ? 'bg-stone-200 dark:bg-stone-700 text-stone-400 dark:text-stone-400 cursor-not-allowed'
                : 'bg-sage-600 hover:bg-sage-700 text-white'
            }`}
          >
            {isRunning ? (
              <><i className="fa-solid fa-spinner fa-spin mr-1" />Running...</>
            ) : (
              <><i className="fa-solid fa-play mr-1" />Run {specCount > 0 ? `${specCount} Test${specCount !== 1 ? 's' : ''}` : 'Tests'}</>
            )}
          </button>
        </div>
      </div>

      {/* Two-panel body */}
      <div className="flex-1 flex min-h-0">
        {/* Left: Test Builder */}
        <div className="w-1/2 border-r border-stone-200 dark:border-stone-700 flex flex-col min-h-0 glass-surface">
          <TestBuilder
            specs={testSpecs}
            onUpdateSpecs={setTestSpecs}
            nodes={nodes}
            links={links}
            disabled={isRunning}
          />
        </div>

        {/* Right: Results */}
        <div className="w-1/2 flex flex-col min-h-0 glass-surface">
          {/* Summary banner */}
          {testSummary && testSummary.total > 0 && (
            <div className={`px-4 py-2 text-xs font-medium flex items-center justify-between flex-shrink-0 ${
              testSummary.failed > 0 || testSummary.errors > 0
                ? 'bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400'
                : 'bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-400'
            }`}>
              <div className="flex items-center gap-3">
                <span>
                  {testSummary.passed}/{testSummary.total} passed
                </span>
                {testSummary.failed > 0 && (
                  <span className="text-red-600 dark:text-red-400">{testSummary.failed} failed</span>
                )}
                {testSummary.errors > 0 && (
                  <span className="text-amber-600 dark:text-amber-400">{testSummary.errors} errors</span>
                )}
                {isComplete && (
                  <span className="text-stone-400 dark:text-stone-500">{formatDuration(totalDuration)}</span>
                )}
              </div>
              {isComplete && (
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-white/50 dark:hover:bg-stone-800/50 transition-colors"
                  title="Download test results as JSON"
                >
                  <i className="fa-solid fa-download" />
                  <span>Download</span>
                </button>
              )}
            </div>
          )}

          {/* Results list */}
          <div className="flex-1 overflow-y-auto">
            {testResults.length === 0 && !isRunning && (
              <div className="flex items-center justify-center h-full text-stone-400 dark:text-stone-400 text-sm">
                <div className="text-center">
                  <i className="fa-solid fa-chart-bar text-2xl mb-2 block" />
                  <p>No results yet</p>
                  <p className="text-xs mt-1">Add tests and click Run</p>
                </div>
              </div>
            )}
            {testResults.map((result, i) => {
              const si = statusIcon[result.status] || statusIcon.error;
              const isExpanded = expandedRows.has(i);
              const hasDetail = !!(result.output || result.error);
              return (
                <div
                  key={`${result.spec_index}-${i}`}
                  className="border-b border-stone-100 dark:border-stone-800"
                >
                  {/* Row header — clickable when there's detail */}
                  <div
                    className={`px-4 py-2.5 flex items-center justify-between transition-colors ${
                      hasDetail ? 'cursor-pointer hover:bg-stone-50 dark:hover:bg-stone-800/50' : ''
                    }`}
                    onClick={hasDetail ? () => toggleRow(i) : undefined}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {hasDetail && (
                        <i className={`fa-solid fa-chevron-right text-[10px] text-stone-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
                      )}
                      <i className={`fa-solid ${si.icon} ${si.color} text-xs`} />
                      <span className="text-sm font-medium text-stone-700 dark:text-stone-200 truncate">
                        {result.spec_name}
                      </span>
                    </div>
                    <span className="text-xs text-stone-400 dark:text-stone-400 tabular-nums flex-shrink-0 ml-2">
                      {formatDuration(result.duration_ms)}
                    </span>
                  </div>

                  {/* Expandable detail */}
                  {isExpanded && hasDetail && (
                    <div className="px-4 pb-3">
                      {result.error && (
                        <div className="text-xs font-mono bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 rounded px-3 py-2 mb-1.5 whitespace-pre-wrap break-words">
                          <span className="font-semibold">Error: </span>{result.error}
                        </div>
                      )}
                      {result.output && (
                        <div className="text-xs font-mono bg-stone-50 dark:bg-stone-800/50 text-stone-600 dark:text-stone-300 rounded px-3 py-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words">
                          {result.output}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {isRunning && testResults.length < (testSummary?.total || 0) && (
              <div className="px-4 py-3 flex items-center gap-2 text-stone-400 dark:text-stone-400 text-sm">
                <i className="fa-solid fa-spinner fa-spin text-xs" />
                Running test {testResults.length + 1} of {testSummary?.total || '?'}...
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default VerificationPanel;
