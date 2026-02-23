import React from 'react';
import { TestResult } from '../types';

interface VerificationPanelProps {
  labId: string;
  testResults: TestResult[];
  testSummary: { total: number; passed: number; failed: number; errors: number } | null;
  isRunning: boolean;
  onStartTests: () => void;
}

const statusIcon: Record<string, { icon: string; color: string }> = {
  passed: { icon: 'fa-check-circle', color: 'text-green-500' },
  failed: { icon: 'fa-times-circle', color: 'text-red-500' },
  error: { icon: 'fa-exclamation-triangle', color: 'text-amber-500' },
  skipped: { icon: 'fa-minus-circle', color: 'text-stone-400' },
};

const VerificationPanel: React.FC<VerificationPanelProps> = ({
  testResults,
  testSummary,
  isRunning,
  onStartTests,
}) => {
  return (
    <div className="h-full flex flex-col bg-white dark:bg-stone-900 border-t border-stone-200 dark:border-stone-700">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-200 dark:border-stone-700">
        <div className="flex items-center gap-2">
          <i className="fa-solid fa-flask text-sage-600 dark:text-sage-400" />
          <span className="text-sm font-semibold text-stone-700 dark:text-stone-200">Lab Verification</span>
        </div>
        <button
          onClick={onStartTests}
          disabled={isRunning}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            isRunning
              ? 'bg-stone-200 dark:bg-stone-700 text-stone-400 dark:text-stone-500 cursor-not-allowed'
              : 'bg-sage-600 hover:bg-sage-700 text-white'
          }`}
        >
          {isRunning ? (
            <>
              <i className="fa-solid fa-spinner fa-spin mr-1" />
              Running...
            </>
          ) : (
            <>
              <i className="fa-solid fa-play mr-1" />
              Run Tests
            </>
          )}
        </button>
      </div>

      {/* Summary banner */}
      {testSummary && testSummary.total > 0 && (
        <div className={`px-4 py-2 text-xs font-medium flex items-center gap-3 ${
          testSummary.failed > 0 || testSummary.errors > 0
            ? 'bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400'
            : 'bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-400'
        }`}>
          <span>
            {testSummary.passed}/{testSummary.total} passed
          </span>
          {testSummary.failed > 0 && (
            <span className="text-red-600 dark:text-red-400">{testSummary.failed} failed</span>
          )}
          {testSummary.errors > 0 && (
            <span className="text-amber-600 dark:text-amber-400">{testSummary.errors} errors</span>
          )}
        </div>
      )}

      {/* Results list */}
      <div className="flex-1 overflow-y-auto">
        {testResults.length === 0 && !isRunning && (
          <div className="flex items-center justify-center h-full text-stone-400 dark:text-stone-500 text-sm">
            <div className="text-center">
              <i className="fa-solid fa-flask text-2xl mb-2 block" />
              <p>No test results yet</p>
              <p className="text-xs mt-1">Run tests or add a <code className="bg-stone-100 dark:bg-stone-800 px-1 rounded">tests:</code> section to your topology YAML</p>
            </div>
          </div>
        )}
        {testResults.map((result, i) => {
          const si = statusIcon[result.status] || statusIcon.error;
          return (
            <div
              key={`${result.spec_index}-${i}`}
              className="px-4 py-2.5 border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/50 transition-colors"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <i className={`fa-solid ${si.icon} ${si.color} text-xs`} />
                  <span className="text-sm font-medium text-stone-700 dark:text-stone-200 truncate">
                    {result.spec_name}
                  </span>
                </div>
                <span className="text-xs text-stone-400 dark:text-stone-500 tabular-nums flex-shrink-0 ml-2">
                  {result.duration_ms.toFixed(0)}ms
                </span>
              </div>
              {(result.error || (result.status === 'failed' && result.output)) && (
                <div className="mt-1 text-xs text-stone-500 dark:text-stone-400 font-mono bg-stone-50 dark:bg-stone-800/50 rounded px-2 py-1 max-h-20 overflow-y-auto whitespace-pre-wrap">
                  {result.error || result.output}
                </div>
              )}
            </div>
          );
        })}
        {isRunning && testResults.length < (testSummary?.total || 0) && (
          <div className="px-4 py-3 flex items-center gap-2 text-stone-400 dark:text-stone-500 text-sm">
            <i className="fa-solid fa-spinner fa-spin text-xs" />
            Running test {testResults.length + 1} of {testSummary?.total || '?'}...
          </div>
        )}
      </div>
    </div>
  );
};

export default VerificationPanel;
