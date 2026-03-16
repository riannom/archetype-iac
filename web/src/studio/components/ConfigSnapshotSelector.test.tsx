import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConfigSnapshotSelector from './ConfigSnapshotSelector';
import type { NodeStateEntry } from '../../types/nodeState';

// ---------------------------------------------------------------------------
// Mock ConfigRebootConfirmModal as a controllable stub
// ---------------------------------------------------------------------------
let capturedModalProps: Record<string, any> = {};

vi.mock('./ConfigRebootConfirmModal', () => ({
  default: (props: any) => {
    capturedModalProps = props;
    if (!props.isOpen) return null;
    return (
      <div data-testid="confirm-modal">
        <span data-testid="modal-description">{props.actionDescription}</span>
        <button data-testid="reboot-now" onClick={props.onRebootNow} disabled={props.loading}>
          Reboot Now
        </button>
        <button data-testid="apply-later" onClick={props.onApplyLater} disabled={props.loading}>
          Apply Later
        </button>
        <button data-testid="modal-close" onClick={props.onClose}>
          Close
        </button>
      </div>
    );
  },
}));

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------
interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  device_kind: string | null;
  created_at: string;
  is_active: boolean;
  is_orphaned: boolean;
}

function makeSnapshot(overrides: Partial<ConfigSnapshot> = {}): ConfigSnapshot {
  return {
    id: 'snap-1',
    lab_id: 'lab-1',
    node_name: 'R1',
    content: 'hostname R1\n!',
    content_hash: 'abc123',
    snapshot_type: 'manual',
    device_kind: 'ceos',
    created_at: new Date(Date.now() - 60000 * 5).toISOString(), // 5 minutes ago
    is_active: false,
    is_orphaned: false,
    ...overrides,
  };
}

const mockStudioRequest = vi.fn();
const mockOnOpenConfigViewer = vi.fn();
const mockOnUpdateStatus = vi.fn();

const defaultProps = {
  labId: 'lab-1',
  nodeName: 'R1',
  nodeId: 'node-1',
  studioRequest: mockStudioRequest,
  onOpenConfigViewer: mockOnOpenConfigViewer,
  onUpdateStatus: mockOnUpdateStatus,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('ConfigSnapshotSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedModalProps = {};
  });

  // -----------------------------------------------------------------------
  // Loading / empty states
  // -----------------------------------------------------------------------
  describe('Loading and empty states', () => {
    it('shows loading skeleton initially', () => {
      mockStudioRequest.mockReturnValue(new Promise(() => {})); // never resolves
      render(<ConfigSnapshotSelector {...defaultProps} />);

      const skeleton = document.querySelector('.skeleton-shimmer');
      expect(skeleton).toBeInTheDocument();
    });

    it('shows empty state when no snapshots exist', async () => {
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('No snapshots')).toBeInTheDocument();
        expect(screen.getByText(/extract configs to create snapshots/i)).toBeInTheDocument();
      });
    });
  });

  // -----------------------------------------------------------------------
  // Snapshot list
  // -----------------------------------------------------------------------
  describe('Snapshot list', () => {
    it('renders snapshot with relative time and type badge', async () => {
      const snap = makeSnapshot({
        snapshot_type: 'manual',
        created_at: new Date(Date.now() - 60000 * 3).toISOString(), // 3 min ago
      });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('3m ago')).toBeInTheDocument();
        expect(screen.getByText('Manual')).toBeInTheDocument();
      });
    });

    it('renders pre_stop type badge correctly', async () => {
      const snap = makeSnapshot({ snapshot_type: 'pre_stop' });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Pre-Stop')).toBeInTheDocument();
      });
    });

    it('shows star icon for active snapshot', async () => {
      const snap = makeSnapshot({ is_active: true });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        // Active snapshot has a star icon
        const star = document.querySelector('.fa-star');
        expect(star).toBeInTheDocument();
      });
    });

    it('clicking a snapshot selects it', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot();
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());

      // Click the snapshot row
      const row = screen.getByText('Manual').closest('div[class*="cursor-pointer"]');
      expect(row).toBeTruthy();
      await user.click(row!);

      // After selection the row should have sage highlight class
      await waitFor(() => {
        expect(row?.className).toContain('sage-500');
      });
    });
  });

  // -----------------------------------------------------------------------
  // Set active (with confirm modal)
  // -----------------------------------------------------------------------
  describe('Set active', () => {
    it('opens confirm modal when set-active button is clicked', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot({ is_active: false });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());

      // Hover to reveal the star button, then click it
      const setActiveBtn = document.querySelector('.fa-star')?.closest('button') ??
        screen.getByTitle('Set as active config');
      await user.click(setActiveBtn!);

      await waitFor(() => {
        expect(screen.getByTestId('confirm-modal')).toBeInTheDocument();
      });
    });

    it('calls PUT with snapshot_id on reboot-now confirm', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot({ id: 'snap-42', is_active: false });
      mockStudioRequest
        .mockResolvedValueOnce({ snapshots: [snap] }) // initial load
        .mockResolvedValueOnce({}) // PUT active-config
        .mockResolvedValueOnce({ snapshots: [{ ...snap, is_active: true }] }); // re-fetch

      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());

      // Click set-active
      const setActiveBtn = screen.getByTitle('Set as active config');
      await user.click(setActiveBtn);

      await waitFor(() => expect(screen.getByTestId('confirm-modal')).toBeInTheDocument());

      // Click "Reboot Now"
      await user.click(screen.getByTestId('reboot-now'));

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          '/labs/lab-1/nodes/R1/active-config',
          expect.objectContaining({
            method: 'PUT',
            body: JSON.stringify({ snapshot_id: 'snap-42' }),
          })
        );
      });

      // onUpdateStatus should be called with booting
      expect(mockOnUpdateStatus).toHaveBeenCalledWith('node-1', 'booting');
    });

    it('calls PUT and sets pending flag on apply-later confirm', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot({ id: 'snap-42', is_active: false });
      mockStudioRequest
        .mockResolvedValueOnce({ snapshots: [snap] })
        .mockResolvedValueOnce({})
        .mockResolvedValueOnce({ snapshots: [{ ...snap, is_active: true }] });

      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());

      const setActiveBtn = screen.getByTitle('Set as active config');
      await user.click(setActiveBtn);

      await waitFor(() => expect(screen.getByTestId('confirm-modal')).toBeInTheDocument());

      await user.click(screen.getByTestId('apply-later'));

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          '/labs/lab-1/nodes/R1/active-config',
          expect.objectContaining({ method: 'PUT' })
        );
      });

      // onUpdateStatus should NOT be called (no reboot)
      expect(mockOnUpdateStatus).not.toHaveBeenCalled();

      // Pending config change badge should appear
      await waitFor(() => {
        expect(screen.getByText(/config change pending/i)).toBeInTheDocument();
      });
    });
  });

  // -----------------------------------------------------------------------
  // Reset to default
  // -----------------------------------------------------------------------
  describe('Reset to default', () => {
    it('shows reset button only when there is an active snapshot', async () => {
      const snap = makeSnapshot({ is_active: true });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Default')).toBeInTheDocument();
      });
    });

    it('does not show reset button when no active snapshot', async () => {
      const snap = makeSnapshot({ is_active: false });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Manual')).toBeInTheDocument();
      });
      expect(screen.queryByText('Default')).not.toBeInTheDocument();
    });

    it('opens modal with null snapshotId on reset click', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot({ is_active: true });
      mockStudioRequest.mockResolvedValueOnce({ snapshots: [snap] });
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Default')).toBeInTheDocument());

      await user.click(screen.getByText('Default'));

      await waitFor(() => {
        expect(screen.getByTestId('confirm-modal')).toBeInTheDocument();
        expect(screen.getByTestId('modal-description').textContent).toContain(
          'Clear the active startup configuration'
        );
      });
    });
  });

  // -----------------------------------------------------------------------
  // Error state
  // -----------------------------------------------------------------------
  describe('Error state', () => {
    it('shows error message when fetch fails', async () => {
      mockStudioRequest.mockRejectedValueOnce(new Error('Network error'));
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Failed to load snapshots')).toBeInTheDocument();
      });
    });

    it('dismiss clears error', async () => {
      const user = userEvent.setup();
      mockStudioRequest.mockRejectedValueOnce(new Error('Network error'));
      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Failed to load snapshots')).toBeInTheDocument());

      // Click the dismiss (x) button
      const dismissBtn = screen.getByText('Failed to load snapshots')
        .closest('div')!
        .querySelector('button');
      expect(dismissBtn).toBeTruthy();
      await user.click(dismissBtn!);

      await waitFor(() => {
        expect(screen.queryByText('Failed to load snapshots')).not.toBeInTheDocument();
      });
    });
  });

  // -----------------------------------------------------------------------
  // Delete snapshot
  // -----------------------------------------------------------------------
  describe('Delete snapshot', () => {
    it('calls DELETE endpoint and refreshes list', async () => {
      const user = userEvent.setup();
      const snap = makeSnapshot({ id: 'snap-del' });
      mockStudioRequest
        .mockResolvedValueOnce({ snapshots: [snap] }) // initial load
        .mockResolvedValueOnce({}) // DELETE
        .mockResolvedValueOnce({ snapshots: [] }); // re-fetch

      render(<ConfigSnapshotSelector {...defaultProps} />);

      await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument());

      // Click the delete (trash) button
      const deleteBtn = screen.getByTitle('Delete snapshot');
      await user.click(deleteBtn);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          '/labs/lab-1/config-snapshots/snap-del',
          expect.objectContaining({ method: 'DELETE' })
        );
      });

      // After deletion, the empty state should appear
      await waitFor(() => {
        expect(screen.getByText('No snapshots')).toBeInTheDocument();
      });
    });
  });
});
