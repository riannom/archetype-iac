import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScenarioPanel from './ScenarioPanel';
import type { ScenarioStepData } from '../hooks/useLabStateWS';

// ---------------------------------------------------------------------------
// Mocks
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

/**
 * Helper: load scenarios list and select a scenario, returning the user
 * event instance. This avoids repeating the same setup in every test.
 */
async function selectScenario(
  props: Partial<typeof defaultProps> = {},
  summary: ScenarioSummary = makeSummary(),
  rawYaml = 'name: Failover Test\nsteps: []',
) {
  const user = userEvent.setup();
  // First call: list, second call: detail
  mockApiRequest
    .mockResolvedValueOnce([summary])
    .mockResolvedValueOnce({
      filename: summary.filename,
      name: summary.name,
      description: summary.description,
      steps: [],
      raw_yaml: rawYaml,
    });

  render(<ScenarioPanel {...defaultProps} {...props} />);

  await waitFor(() => expect(screen.getByText(summary.name)).toBeInTheDocument());
  await user.click(screen.getByText(summary.name));

  // Wait for detail to load (textarea or header appears)
  await waitFor(() =>
    expect(mockApiRequest).toHaveBeenCalledWith(`/labs/lab-1/scenarios/${summary.filename}`),
  );

  return user;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('ScenarioPanel — round 12', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockResolvedValue([]);
  });

  // -----------------------------------------------------------------------
  // YAML dirty state detection
  // -----------------------------------------------------------------------
  describe('YAML dirty state detection', () => {
    it('marks editor dirty when content is changed', async () => {
      const user = await selectScenario();

      // Editor should be visible (textarea)
      const textarea = screen.getByRole('textbox');
      await user.type(textarea, ' modified');

      // UNSAVED badge should appear
      expect(screen.getByText('UNSAVED')).toBeInTheDocument();
    });

    it('does not show UNSAVED badge initially after selecting a scenario', async () => {
      await selectScenario();

      // No UNSAVED indicator should be present
      expect(screen.queryByText('UNSAVED')).not.toBeInTheDocument();
    });

    it('clears dirty state when switching to a different scenario', async () => {
      const user = userEvent.setup();

      const summaries = [
        makeSummary({ filename: 'a.yml', name: 'Alpha' }),
        makeSummary({ filename: 'b.yml', name: 'Beta' }),
      ];

      // First call: list; second: detail for Alpha; third: detail for Beta
      mockApiRequest
        .mockResolvedValueOnce(summaries)
        .mockResolvedValueOnce({ filename: 'a.yml', name: 'Alpha', description: '', steps: [], raw_yaml: 'original' })
        .mockResolvedValueOnce({ filename: 'b.yml', name: 'Beta', description: '', steps: [], raw_yaml: 'beta yaml' });

      render(<ScenarioPanel {...defaultProps} />);

      // Select Alpha
      await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());
      await user.click(screen.getByText('Alpha'));
      await waitFor(() => expect(screen.getByRole('textbox')).toBeInTheDocument());

      // Dirty the editor
      await user.type(screen.getByRole('textbox'), ' dirty');
      expect(screen.getByText('UNSAVED')).toBeInTheDocument();

      // Switch to Beta — dirty state should reset
      await user.click(screen.getByText('Beta'));
      await waitFor(() => expect(screen.queryByText('UNSAVED')).not.toBeInTheDocument());
    });

    it('disables Run button when editor is dirty', async () => {
      const user = await selectScenario();

      const textarea = screen.getByRole('textbox');
      await user.type(textarea, 'x');

      const runBtn = screen.getByRole('button', { name: /run/i });
      expect(runBtn).toBeDisabled();
    });
  });

  // -----------------------------------------------------------------------
  // Save flow
  // -----------------------------------------------------------------------
  describe('Save flow', () => {
    it('shows Save button only when editor is dirty', async () => {
      const user = await selectScenario();

      // Initially no Save button
      expect(screen.queryByRole('button', { name: /^save$/i })).not.toBeInTheDocument();

      // Type to make dirty
      await user.type(screen.getByRole('textbox'), ' x');
      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    });

    it('sends PUT with editor content on save', async () => {
      const user = await selectScenario({}, makeSummary(), 'original');

      const textarea = screen.getByRole('textbox');
      // Clear and replace content
      await user.clear(textarea);
      await user.type(textarea, 'updated yaml');

      // Set up mocks for the save and subsequent loadScenarios call
      mockRawApiRequest.mockResolvedValueOnce({ ok: true, text: async () => '' } as Response);
      mockApiRequest.mockResolvedValueOnce([makeSummary()]);

      const saveBtn = screen.getByRole('button', { name: /save/i });
      await user.click(saveBtn);

      await waitFor(() => {
        expect(mockRawApiRequest).toHaveBeenCalledWith(
          '/labs/lab-1/scenarios/failover.yml',
          expect.objectContaining({
            method: 'PUT',
            body: JSON.stringify({ content: 'updated yaml' }),
          }),
        );
      });
    });

    it('clears dirty state after successful save', async () => {
      const user = await selectScenario();

      await user.type(screen.getByRole('textbox'), ' x');
      expect(screen.getByText('UNSAVED')).toBeInTheDocument();

      // Set up mocks for save and subsequent loadScenarios
      mockRawApiRequest.mockResolvedValueOnce({ ok: true, text: async () => '' } as Response);
      mockApiRequest.mockResolvedValueOnce([makeSummary()]);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(screen.queryByText('UNSAVED')).not.toBeInTheDocument();
      });
    });

    it('shows alert on save failure and keeps dirty state', async () => {
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

      const user = await selectScenario();

      await user.type(screen.getByRole('textbox'), ' x');

      mockRawApiRequest.mockResolvedValueOnce({
        ok: false,
        text: async () => 'Validation error',
      } as Response);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(alertSpy).toHaveBeenCalledWith('Validation error');
      });

      // Dirty state should persist
      expect(screen.getByText('UNSAVED')).toBeInTheDocument();

      alertSpy.mockRestore();
    });

    it('shows Saving... text while save is in progress', async () => {
      const user = await selectScenario();

      await user.type(screen.getByRole('textbox'), ' x');

      // Create a deferred promise so we can control when save resolves
      let resolveSave!: (value: Response) => void;
      const savePromise = new Promise<Response>((resolve) => { resolveSave = resolve; });
      mockRawApiRequest.mockReturnValueOnce(savePromise);

      await user.click(screen.getByRole('button', { name: /save/i }));

      // While in flight, button should show "Saving..."
      expect(screen.getByRole('button', { name: /saving/i })).toBeInTheDocument();

      // Resolve
      resolveSave({ ok: true, text: async () => '' } as Response);
      mockApiRequest.mockResolvedValueOnce([makeSummary()]);

      await waitFor(() => {
        expect(screen.queryByText(/saving/i)).not.toBeInTheDocument();
      });
    });
  });

  // -----------------------------------------------------------------------
  // Step type badges
  // -----------------------------------------------------------------------
  describe('Step type badges', () => {
    const badgeCases: Array<{ type: string; label: string }> = [
      { type: 'verify', label: 'VERIFY' },
      { type: 'link_down', label: 'LINK DOWN' },
      { type: 'link_up', label: 'LINK UP' },
      { type: 'node_stop', label: 'STOP' },
      { type: 'node_start', label: 'START' },
      { type: 'wait', label: 'WAIT' },
      { type: 'exec', label: 'EXEC' },
    ];

    it.each(badgeCases)(
      'renders "$label" badge for step type "$type"',
      async ({ type, label }) => {
        const steps: ScenarioStepData[] = [
          makeStep({ step_index: 0, step_name: `Step ${type}`, step_type: type, status: 'passed' }),
        ];

        await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

        await waitFor(() => {
          expect(screen.getByText(label)).toBeInTheDocument();
        });
      },
    );

    it('does not render a badge for unknown step types', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Custom step', step_type: 'custom_unknown', status: 'passed' }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => {
        expect(screen.getByText('Custom step')).toBeInTheDocument();
      });

      // None of the known badge labels should appear
      for (const { label } of badgeCases) {
        expect(screen.queryByText(label)).not.toBeInTheDocument();
      }
    });
  });

  // -----------------------------------------------------------------------
  // Execution status / timeline
  // -----------------------------------------------------------------------
  describe('Execution status', () => {
    it('shows progress bar with step counter while running', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Step 1', status: 'passed', total_steps: 3 }),
        makeStep({ step_index: 1, step_name: 'Step 2', status: 'running', total_steps: 3 }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => {
        // "Step 2 of 3" — activeStepIndex is 1, so displayed as 2
        expect(screen.getByText(/Step 2 of 3/)).toBeInTheDocument();
      });
    });

    it('shows Running... on the Run button when scenario is active', async () => {
      await selectScenario({ activeScenarioJobId: 'job-running' });

      const runBtn = screen.getByRole('button', { name: /running/i });
      expect(runBtn).toBeDisabled();
    });

    it('shows duration in milliseconds for short steps', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Fast step', status: 'passed', duration_ms: 450 }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => {
        expect(screen.getByText('450ms')).toBeInTheDocument();
      });
    });

    it('shows duration in seconds for long steps', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Slow step', status: 'passed', duration_ms: 2500 }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => {
        expect(screen.getByText('2.5s')).toBeInTheDocument();
      });
    });

    it('shows overall passed badge when scenario completes successfully', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Check', status: 'passed' }),
        makeStep({ step_index: -1, step_name: '', step_type: '', status: 'passed', total_steps: 1 }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: null });

      await waitFor(() => {
        expect(screen.getByText('passed')).toBeInTheDocument();
      });
    });

    it('shows overall failed badge when scenario fails', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Check', status: 'failed' }),
        makeStep({ step_index: -1, step_name: '', step_type: '', status: 'failed', total_steps: 1 }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: null });

      await waitFor(() => {
        expect(screen.getByText('failed')).toBeInTheDocument();
      });
    });

    it('shows "Edit scenario YAML" button when not running and timeline is visible', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Done step', status: 'passed' }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: null });

      await waitFor(() => {
        expect(screen.getByText(/Edit scenario YAML/)).toBeInTheDocument();
      });
    });

    it('hides "Edit scenario YAML" button while running', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({ step_index: 0, step_name: 'Running step', status: 'running' }),
      ];

      await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => {
        expect(screen.getByText('Running step')).toBeInTheDocument();
      });

      expect(screen.queryByText(/Edit scenario YAML/)).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Error display
  // -----------------------------------------------------------------------
  describe('Error display', () => {
    it('shows error text when a step is expanded and has an error', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({
          step_index: 0,
          step_name: 'Failing step',
          status: 'failed',
          error: 'Connection refused to 10.0.0.1',
        }),
      ];

      const user = await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      // Click step to expand
      await waitFor(() => expect(screen.getByText('Failing step')).toBeInTheDocument());
      await user.click(screen.getByText('Failing step'));

      await waitFor(() => {
        expect(screen.getByText('Connection refused to 10.0.0.1')).toBeInTheDocument();
      });
    });

    it('shows output text when a step is expanded and has output', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({
          step_index: 0,
          step_name: 'Verbose step',
          status: 'passed',
          output: 'PING 10.0.0.1: 64 bytes from 10.0.0.1',
        }),
      ];

      const user = await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => expect(screen.getByText('Verbose step')).toBeInTheDocument());
      await user.click(screen.getByText('Verbose step'));

      await waitFor(() => {
        expect(screen.getByText('PING 10.0.0.1: 64 bytes from 10.0.0.1')).toBeInTheDocument();
      });
    });

    it('toggles expanded state on repeated clicks', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({
          step_index: 0,
          step_name: 'Toggle step',
          status: 'failed',
          error: 'Some error detail',
        }),
      ];

      const user = await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => expect(screen.getByText('Toggle step')).toBeInTheDocument());

      // Expand
      await user.click(screen.getByText('Toggle step'));
      await waitFor(() => expect(screen.getByText('Some error detail')).toBeInTheDocument());

      // Collapse
      await user.click(screen.getByText('Toggle step'));
      await waitFor(() => expect(screen.queryByText('Some error detail')).not.toBeInTheDocument());
    });

    it('does not expand steps without output or error', async () => {
      const steps: ScenarioStepData[] = [
        makeStep({
          step_index: 0,
          step_name: 'Clean step',
          status: 'passed',
          output: undefined,
          error: undefined,
        }),
      ];

      const user = await selectScenario({ scenarioSteps: steps, activeScenarioJobId: 'job-1' });

      await waitFor(() => expect(screen.getByText('Clean step')).toBeInTheDocument());

      // Clicking should not produce any expanded content (no chevron, no detail)
      await user.click(screen.getByText('Clean step'));

      // The step container should not have an expanded detail section
      // Since there's no output or error, there should be no <pre> or error div
      expect(screen.queryByRole('pre')).not.toBeInTheDocument();
    });

    it('shows error from failed scenario load in the editor', async () => {
      const user = userEvent.setup();

      mockApiRequest
        .mockResolvedValueOnce([makeSummary()])
        .mockRejectedValueOnce(new Error('Network error'));

      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Failover Test')).toBeInTheDocument());
      await user.click(screen.getByText('Failover Test'));

      // On load failure, editor shows fallback text
      await waitFor(() => {
        const textarea = screen.getByRole('textbox');
        expect(textarea).toHaveValue('# Failed to load scenario');
      });
    });
  });

  // -----------------------------------------------------------------------
  // Empty / placeholder state
  // -----------------------------------------------------------------------
  describe('Placeholder state', () => {
    it('shows placeholder when no scenario is selected', () => {
      mockApiRequest.mockResolvedValueOnce([]);
      render(<ScenarioPanel {...defaultProps} />);

      expect(screen.getByText('Select or create a scenario')).toBeInTheDocument();
      expect(screen.getByText(/step-by-step network test sequences/)).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Scenario description in sidebar
  // -----------------------------------------------------------------------
  describe('Sidebar description', () => {
    it('shows description alongside step count', async () => {
      mockApiRequest.mockResolvedValueOnce([
        makeSummary({ step_count: 2, description: 'OSPF convergence' }),
      ]);
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/2 steps/)).toBeInTheDocument();
        expect(screen.getByText(/OSPF convergence/)).toBeInTheDocument();
      });
    });

    it('uses singular "step" for single-step scenarios', async () => {
      mockApiRequest.mockResolvedValueOnce([
        makeSummary({ step_count: 1, description: '' }),
      ]);
      render(<ScenarioPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('1 step')).toBeInTheDocument();
      });
    });
  });
});
