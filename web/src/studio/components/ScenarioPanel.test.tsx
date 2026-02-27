import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScenarioPanel from './ScenarioPanel';
import type { ScenarioStepData } from '../hooks/useLabStateWS';

// ---------------------------------------------------------------------------
// Mock the API module
// ---------------------------------------------------------------------------
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
  rawApiRequest: vi.fn(),
  API_BASE_URL: '/api',
}));

import { apiRequest, rawApiRequest } from '../../api';

const mockApiRequest = vi.mocked(apiRequest);
const mockRawApiRequest = vi.mocked(rawApiRequest);

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------
interface ScenarioSummary {
  filename: string;
  name: string;
  description: string;
  step_count: number;
}

function makeSummary(overrides: Partial<ScenarioSummary> = {}): ScenarioSummary {
  return {
    filename: 'failover.yml',
    name: 'Failover Test',
    description: 'Test link failover',
    step_count: 3,
    ...overrides,
  };
}

function makeStep(overrides: Partial<ScenarioStepData> = {}): ScenarioStepData {
  return {
    job_id: 'job-1',
    step_index: 0,
    step_name: 'Baseline ping',
    step_type: 'verify',
    status: 'passed',
    total_steps: 3,
    duration_ms: 120,
    ...overrides,
  };
}

const defaultProps = {
  labId: 'lab-1',
  scenarioSteps: [] as ScenarioStepData[],
  activeScenarioJobId: null as string | null,
  onStartScenario: vi.fn(),
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('ScenarioPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no scenarios loaded
    mockApiRequest.mockResolvedValue([]);
  });

  // -----------------------------------------------------------------------
  // Scenario list loading
  // -----------------------------------------------------------------------
  describe('Scenario list', () => {
    it('loads scenario list on mount', async () => {
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith('/labs/lab-1/scenarios');
      });
    });

    it('shows empty state when no scenarios exist', async () => {
      mockApiRequest.mockResolvedValueOnce([]);
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('No scenarios yet')).toBeInTheDocument();
      });
    });

    it('renders scenario names in the sidebar', async () => {
      mockApiRequest.mockResolvedValueOnce([
        makeSummary({ filename: 'a.yml', name: 'Alpha Test' }),
        makeSummary({ filename: 'b.yml', name: 'Beta Test' }),
      ]);
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Alpha Test')).toBeInTheDocument();
        expect(screen.getByText('Beta Test')).toBeInTheDocument();
      });
    });

    it('shows step count for each scenario', async () => {
      mockApiRequest.mockResolvedValueOnce([
        makeSummary({ step_count: 5 }),
      ]);
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/5 steps/)).toBeInTheDocument();
      });
    });
  });

  // -----------------------------------------------------------------------
  // Select scenario
  // -----------------------------------------------------------------------
  describe('Select scenario', () => {
    it('loads YAML when a scenario is clicked', async () => {
      const user = userEvent.setup();
      // First call: load list; second call: load scenario detail
      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({
          filename: 'failover.yml',
          name: 'Failover Test',
          description: 'desc',
          steps: [],
          raw_yaml: 'name: Failover Test\nsteps: []',
        });

      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith('/labs/lab-1/scenarios/failover.yml');
      });
    });

    it('highlights the selected scenario in the sidebar', async () => {
      const user = userEvent.setup();
      mockApiRequest
        .mockResolvedValueOnce([makeSummary({ filename: 'a.yml', name: 'Alpha' })])
        .mockResolvedValueOnce({ filename: 'a.yml', name: 'Alpha', description: '', steps: [], raw_yaml: '' });

      render(<ScenarioPanel {...defaultProps} />);

      // Wait for list to load then click the first matching element
      const items = await screen.findAllByText('Alpha');
      await user.click(items[0]);

      await waitFor(() => {
        // The selected item gets a distinctive CSS class
        const item = items[0].closest('div[class*="cursor-pointer"]');
        expect(item?.className).toContain('sage');
      });
    });
  });

  // -----------------------------------------------------------------------
  // Run button
  // -----------------------------------------------------------------------
  describe('Run button', () => {
    it('calls onStartScenario with the selected filename', async () => {
      const onStartScenario = vi.fn();
      const user = userEvent.setup();
      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({ filename: 'failover.yml', name: 'Failover Test', description: '', steps: [], raw_yaml: '' });

      render(<ScenarioPanel {...defaultProps} onStartScenario={onStartScenario} />);

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      await waitFor(() => expect(screen.getByRole('button', { name: /run/i })).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: /run/i }));

      expect(onStartScenario).toHaveBeenCalledWith('failover.yml');
    });

    it('is disabled when a scenario is running', async () => {
      const user = userEvent.setup();
      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({ filename: 'failover.yml', name: 'Failover Test', description: '', steps: [], raw_yaml: '' });

      render(
        <ScenarioPanel
          {...defaultProps}
          activeScenarioJobId="job-running"
        />
      );

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      await waitFor(() => {
        const runBtn = screen.getByRole('button', { name: /running/i });
        expect(runBtn).toBeDisabled();
      });
    });

    it('is disabled when editor has unsaved changes', async () => {
      const user = userEvent.setup();
      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({ filename: 'failover.yml', name: 'Failover Test', description: '', steps: [], raw_yaml: 'original' });

      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      // Wait for the textarea (editor) to appear, then type to make dirty
      await waitFor(() => expect(screen.getByRole('textbox')).toBeInTheDocument());
      await user.type(screen.getByRole('textbox'), ' changed');

      // Run button should be disabled because editor is dirty
      const runBtn = screen.getByRole('button', { name: /run/i });
      expect(runBtn).toBeDisabled();
    });
  });

  // -----------------------------------------------------------------------
  // Timeline
  // -----------------------------------------------------------------------
  describe('Timeline', () => {
    it('shows steps with type badges when running', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Baseline ping', step_type: 'verify', status: 'passed' }),
        makeStep({ step_index: 1, step_name: 'Kill link', step_type: 'link_down', status: 'running' }),
      ];

      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({ filename: 'failover.yml', name: 'Failover Test', description: '', steps: [], raw_yaml: '' });

      const user = userEvent.setup();
      render(
        <ScenarioPanel
          {...defaultProps}
          scenarioSteps={steps}
          activeScenarioJobId="job-1"
        />
      );

      // Select the scenario to show the timeline
      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      await waitFor(() => {
        expect(screen.getByText('Baseline ping')).toBeInTheDocument();
        expect(screen.getByText('Kill link')).toBeInTheDocument();
        expect(screen.getByText('VERIFY')).toBeInTheDocument();
        expect(screen.getByText('LINK DOWN')).toBeInTheDocument();
      });
    });

    it('shows completion status badge when scenario finishes', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Check', step_type: 'verify', status: 'passed' }),
        // step_index -1 is the completion signal
        makeStep({ step_index: -1, step_name: '', step_type: '', status: 'passed', total_steps: 1 }),
      ];

      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockResolvedValueOnce({ filename: 'failover.yml', name: 'Failover Test', description: '', steps: [], raw_yaml: '' });

      const user = userEvent.setup();
      render(
        <ScenarioPanel
          {...defaultProps}
          scenarioSteps={steps}
          activeScenarioJobId={null}
        />
      );

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      await waitFor(() => {
        // The overall status badge should show "passed"
        expect(screen.getByText('passed')).toBeInTheDocument();
      });
    });
  });
});
