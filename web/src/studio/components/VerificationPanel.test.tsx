import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VerificationPanel from './VerificationPanel';
import { TestResult, TestSpec, Node, Link, DeviceType, DeviceNode } from '../types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
  rawApiRequest: vi.fn(),
  API_BASE_URL: '/api',
}));

vi.mock('./TestBuilder', () => ({
  default: ({ specs, onUpdateSpecs, disabled }: any) => (
    <div data-testid="test-builder">
      <span data-testid="test-builder-count">{specs.length}</span>
      <button
        data-testid="add-spec"
        disabled={disabled}
        onClick={() => onUpdateSpecs([...specs, { type: 'ping', source: 'R1', target: '10.0.0.1' }])}
      >
        Add Spec
      </button>
    </div>
  ),
}));

// Mock usePersistedState to use simple useState (avoids localStorage side effects)
vi.mock('../hooks/usePersistedState', () => ({
  usePersistedState: <T,>(_key: string, defaultValue: T): [T, (v: T | ((prev: T) => T)) => void] => {
    const [state, setState] = React.useState<T>(defaultValue);
    return [state, setState];
  },
}));

import { apiRequest } from '../../api';

const mockApiRequest = vi.mocked(apiRequest);

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------
function makeNode(overrides: Partial<DeviceNode> = {}): DeviceNode {
  return {
    id: 'node-1',
    nodeType: 'device',
    name: 'R1',
    type: DeviceType.ROUTER,
    model: 'ceos',
    version: 'latest',
    x: 0,
    y: 0,
    ...overrides,
  };
}

function makeResult(overrides: Partial<TestResult> = {}): TestResult {
  return {
    spec_index: 0,
    spec_name: 'Ping R1 -> R2',
    status: 'passed',
    duration_ms: 123,
    ...overrides,
  };
}

const defaultProps = {
  labId: 'lab-1',
  testResults: [] as TestResult[],
  testSummary: null as { total: number; passed: number; failed: number; errors: number } | null,
  isRunning: false,
  onStartTests: vi.fn(),
  nodes: [makeNode()] as Node[],
  links: [] as Link[],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('VerificationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -----------------------------------------------------------------------
  // Run button
  // -----------------------------------------------------------------------
  describe('Run button', () => {
    it('fires onStartTests when clicked', async () => {
      const onStartTests = vi.fn();
      const user = userEvent.setup();
      render(<VerificationPanel {...defaultProps} onStartTests={onStartTests} />);

      await user.click(screen.getByRole('button', { name: /run tests/i }));
      expect(onStartTests).toHaveBeenCalledOnce();
    });

    it('fires onStartTests with specs when specs exist', async () => {
      const onStartTests = vi.fn();
      const user = userEvent.setup();
      render(<VerificationPanel {...defaultProps} onStartTests={onStartTests} />);

      // Add a test spec via the TestBuilder stub
      await user.click(screen.getByTestId('add-spec'));

      // Now click Run – should pass the specs
      await user.click(screen.getByRole('button', { name: /run 1 test/i }));
      expect(onStartTests).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ type: 'ping' })])
      );
    });

    it('is disabled when isRunning is true', () => {
      render(<VerificationPanel {...defaultProps} isRunning={true} />);

      const runButton = screen.getByRole('button', { name: /running/i });
      expect(runButton).toBeDisabled();
    });

    it('shows spinner text when running', () => {
      render(<VerificationPanel {...defaultProps} isRunning={true} />);

      expect(screen.getByText(/running\.\.\./i)).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Import from YAML
  // -----------------------------------------------------------------------
  describe('Import from YAML', () => {
    it('calls API and updates specs on success', async () => {
      const user = userEvent.setup();
      mockApiRequest.mockResolvedValueOnce({
        tests: [{ type: 'ping', source: 'R1', target: '10.0.0.2', name: 'From YAML' }],
      });

      render(<VerificationPanel {...defaultProps} />);

      await user.click(screen.getByRole('button', { name: /import from yaml/i }));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith('/labs/lab-1/tests');
      });
    });

    it('is disabled when running', () => {
      render(<VerificationPanel {...defaultProps} isRunning={true} />);

      const importButton = screen.getByRole('button', { name: /importing|import from yaml/i });
      expect(importButton).toBeDisabled();
    });
  });

  // -----------------------------------------------------------------------
  // Results list
  // -----------------------------------------------------------------------
  describe('Results list', () => {
    it('shows empty state when no results and not running', () => {
      render(<VerificationPanel {...defaultProps} />);

      expect(screen.getByText('No results yet')).toBeInTheDocument();
      expect(screen.getByText(/add tests and click run/i)).toBeInTheDocument();
    });

    it('renders passed result with spec name and duration', () => {
      const results = [makeResult({ spec_name: 'Ping check', status: 'passed', duration_ms: 42 })];
      render(<VerificationPanel {...defaultProps} testResults={results} />);

      expect(screen.getByText('Ping check')).toBeInTheDocument();
      expect(screen.getByText('42ms')).toBeInTheDocument();
    });

    it('renders failed result with error details', () => {
      const results = [
        makeResult({ spec_name: 'Ping fail', status: 'failed', duration_ms: 500, output: 'timeout reached' }),
      ];
      render(<VerificationPanel {...defaultProps} testResults={results} />);

      expect(screen.getByText('Ping fail')).toBeInTheDocument();
      expect(screen.getByText('timeout reached')).toBeInTheDocument();
    });

    it('renders error result', () => {
      const results = [
        makeResult({ spec_name: 'Bad test', status: 'error', duration_ms: 10, error: 'connection refused' }),
      ];
      render(<VerificationPanel {...defaultProps} testResults={results} />);

      expect(screen.getByText('Bad test')).toBeInTheDocument();
      expect(screen.getByText('connection refused')).toBeInTheDocument();
    });

    it('shows running progress indicator while tests in progress', () => {
      const summary = { total: 3, passed: 1, failed: 0, errors: 0 };
      const results = [makeResult()];
      render(
        <VerificationPanel
          {...defaultProps}
          isRunning={true}
          testResults={results}
          testSummary={summary}
        />
      );

      expect(screen.getByText(/running test 2 of 3/i)).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Summary banner
  // -----------------------------------------------------------------------
  describe('Summary banner', () => {
    it('shows passed/total count', () => {
      const summary = { total: 5, passed: 4, failed: 1, errors: 0 };
      render(<VerificationPanel {...defaultProps} testSummary={summary} />);

      expect(screen.getByText('4/5 passed')).toBeInTheDocument();
    });

    it('shows failure count when there are failures', () => {
      const summary = { total: 5, passed: 3, failed: 2, errors: 0 };
      render(<VerificationPanel {...defaultProps} testSummary={summary} />);

      expect(screen.getByText('2 failed')).toBeInTheDocument();
    });

    it('shows error count when there are errors', () => {
      const summary = { total: 5, passed: 3, failed: 0, errors: 2 };
      render(<VerificationPanel {...defaultProps} testSummary={summary} />);

      expect(screen.getByText('2 errors')).toBeInTheDocument();
    });

    it('is hidden when testSummary is null', () => {
      render(<VerificationPanel {...defaultProps} testSummary={null} />);

      expect(screen.queryByText(/passed/)).not.toBeInTheDocument();
    });
  });
});
