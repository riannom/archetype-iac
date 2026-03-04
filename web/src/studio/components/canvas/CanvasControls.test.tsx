import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { CanvasControls } from './CanvasControls';

// ============================================================================
// Helpers
// ============================================================================

function defaultProps() {
  return {
    setZoom: vi.fn(),
    centerCanvas: vi.fn(),
    fitToScreen: vi.fn(),
    agents: [] as { id: string; name: string }[],
    showAgentIndicators: false,
    onToggleAgentIndicators: vi.fn(),
  };
}

describe('CanvasControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Zoom buttons ──

  it('renders zoom in and zoom out buttons', () => {
    const { container } = render(<CanvasControls {...defaultProps()} />);
    const buttons = container.querySelectorAll('button');
    // At minimum: zoom in, zoom out, center, fit = 4 buttons
    expect(buttons.length).toBeGreaterThanOrEqual(4);
  });

  it('calls setZoom with increasing updater on zoom in click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<CanvasControls {...props} />);

    const zoomInBtn = container.querySelector('button:has(.fa-plus)')!;
    await user.click(zoomInBtn);

    expect(props.setZoom).toHaveBeenCalledTimes(1);
    // setZoom receives a function (prev => Math.min(prev * 1.2, 5))
    const setZoomArg = props.setZoom.mock.calls[0][0];
    expect(typeof setZoomArg).toBe('function');
    expect(setZoomArg(1)).toBeCloseTo(1.2);
    expect(setZoomArg(4.5)).toBe(5); // capped at 5
  });

  it('calls setZoom with decreasing updater on zoom out click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<CanvasControls {...props} />);

    const zoomOutBtn = container.querySelector('button:has(.fa-minus)')!;
    await user.click(zoomOutBtn);

    expect(props.setZoom).toHaveBeenCalledTimes(1);
    const setZoomArg = props.setZoom.mock.calls[0][0];
    expect(typeof setZoomArg).toBe('function');
    expect(setZoomArg(1)).toBeCloseTo(1 / 1.2);
    expect(setZoomArg(0.12)).toBeCloseTo(0.1); // capped at 0.1
  });

  // ── Center and fit buttons ──

  it('calls centerCanvas when center button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<CanvasControls {...props} />);

    const centerBtn = container.querySelector('button[title="Center (zoom out if needed)"]')!;
    await user.click(centerBtn);

    expect(props.centerCanvas).toHaveBeenCalledTimes(1);
  });

  it('calls fitToScreen when fit button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<CanvasControls {...props} />);

    const fitBtn = container.querySelector('button[title="Fit to screen"]')!;
    await user.click(fitBtn);

    expect(props.fitToScreen).toHaveBeenCalledTimes(1);
  });

  // ── Agent indicator toggle ──

  it('does not render agent indicator toggle when agents.length <= 1', () => {
    const props = defaultProps();
    props.agents = [{ id: 'a1', name: 'Agent-01' }];
    const { container } = render(<CanvasControls {...props} />);

    const serverIcon = container.querySelector('.fa-server');
    expect(serverIcon).not.toBeInTheDocument();
  });

  it('does not render agent indicator toggle when agents is empty', () => {
    const props = defaultProps();
    props.agents = [];
    const { container } = render(<CanvasControls {...props} />);

    const serverIcon = container.querySelector('.fa-server');
    expect(serverIcon).not.toBeInTheDocument();
  });

  it('renders agent indicator toggle when agents.length > 1', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    const { container } = render(<CanvasControls {...props} />);

    const serverIcon = container.querySelector('.fa-server');
    expect(serverIcon).toBeInTheDocument();
  });

  it('calls onToggleAgentIndicators when agent toggle is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    const { container } = render(<CanvasControls {...props} />);

    const toggleBtn = container.querySelector('.fa-server')!.closest('button')!;
    await user.click(toggleBtn);

    expect(props.onToggleAgentIndicators).toHaveBeenCalledTimes(1);
  });

  it('applies active styling when showAgentIndicators is true', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    props.showAgentIndicators = true;
    const { container } = render(<CanvasControls {...props} />);

    const toggleBtn = container.querySelector('.fa-server')!.closest('button')!;
    expect(toggleBtn.className).toContain('text-sage-600');
  });

  it('applies inactive styling when showAgentIndicators is false', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    props.showAgentIndicators = false;
    const { container } = render(<CanvasControls {...props} />);

    const toggleBtn = container.querySelector('.fa-server')!.closest('button')!;
    expect(toggleBtn.className).toContain('text-stone-500');
  });

  it('shows "Hide agent indicators" title when active', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    props.showAgentIndicators = true;
    const { container } = render(<CanvasControls {...props} />);

    const toggleBtn = container.querySelector('.fa-server')!.closest('button')!;
    expect(toggleBtn.title).toBe('Hide agent indicators');
  });

  it('shows "Show agent indicators" title when inactive', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    props.showAgentIndicators = false;
    const { container } = render(<CanvasControls {...props} />);

    const toggleBtn = container.querySelector('.fa-server')!.closest('button')!;
    expect(toggleBtn.title).toBe('Show agent indicators');
  });

  // ── Edge case: no onToggleAgentIndicators ──

  it('does not render agent toggle when onToggleAgentIndicators is undefined', () => {
    const props = defaultProps();
    props.agents = [
      { id: 'a1', name: 'Agent-01' },
      { id: 'a2', name: 'Agent-02' },
    ];
    props.onToggleAgentIndicators = undefined;
    const { container } = render(<CanvasControls {...props} />);

    const serverIcon = container.querySelector('.fa-server');
    expect(serverIcon).not.toBeInTheDocument();
  });
});
