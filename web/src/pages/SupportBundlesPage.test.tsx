import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import SupportBundlesPage from './SupportBundlesPage';

const mockNavigate = vi.fn();
let mockCanManageUsers = true;

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: { id: 'u1', username: 'admin', global_role: 'super_admin', is_active: true },
    loading: false,
  }),
}));

vi.mock('../utils/permissions', () => ({
  canManageUsers: () => mockCanManageUsers,
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div>AdminMenuButton</div>,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('../utils/download', () => ({
  downloadBlob: vi.fn(),
}));

const mockApiRequest = vi.fn();
const mockCreateSupportBundle = vi.fn();
const mockGetSupportBundle = vi.fn();
const mockListSupportBundles = vi.fn();
const mockRawApiRequest = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => mockApiRequest(...args),
  createSupportBundle: (...args: unknown[]) => mockCreateSupportBundle(...args),
  getSupportBundle: (...args: unknown[]) => mockGetSupportBundle(...args),
  listSupportBundles: (...args: unknown[]) => mockListSupportBundles(...args),
  rawApiRequest: (...args: unknown[]) => mockRawApiRequest(...args),
}));

const sampleHistory = [
  {
    id: 'bundle-1',
    created_at: '2026-02-01T10:00:00Z',
    status: 'completed',
    size_bytes: 10485760,
    error_message: null,
    summary: 'Test bundle 1',
  },
  {
    id: 'bundle-2',
    created_at: '2026-02-02T10:00:00Z',
    status: 'failed',
    size_bytes: null,
    error_message: 'Agent unreachable',
    summary: 'Test bundle 2',
  },
  {
    id: 'bundle-3',
    created_at: '2026-02-03T10:00:00Z',
    status: 'running',
    size_bytes: null,
    error_message: null,
    summary: 'Test bundle 3',
  },
];

function setupDefaultMocks() {
  mockApiRequest.mockImplementation(async (path: string) => {
    if (path === '/labs') return { labs: [{ id: 'lab-1', name: 'Test Lab' }] };
    if (path === '/agents') return [{ id: 'agent-1', name: 'Agent 1' }];
    return {};
  });
  mockListSupportBundles.mockResolvedValue(sampleHistory);
  mockCreateSupportBundle.mockResolvedValue({
    id: 'bundle-new',
    created_at: new Date().toISOString(),
    status: 'pending',
    size_bytes: null,
    error_message: null,
  });
}

describe('SupportBundlesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCanManageUsers = true;
    setupDefaultMocks();
  });

  // ============================================================
  // Initial load
  // ============================================================

  it('loads labs, agents, and history on mount', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(mockApiRequest).toHaveBeenCalledWith('/labs');
      expect(mockApiRequest).toHaveBeenCalledWith('/agents');
      expect(mockListSupportBundles).toHaveBeenCalledWith(30);
    });
  });

  it('renders page header', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('SUPPORT BUNDLES')).toBeInTheDocument();
    });
  });

  it('shows loading state initially', async () => {
    // Use slow-resolving promises
    mockApiRequest.mockImplementation(() => new Promise(() => {}));
    mockListSupportBundles.mockImplementation(() => new Promise(() => {}));

    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });
  });

  it('shows error message on load failure', async () => {
    mockApiRequest.mockRejectedValue(new Error('Connection refused'));
    mockListSupportBundles.mockRejectedValue(new Error('Connection refused'));

    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Connection refused')).toBeInTheDocument();
    });
  });

  // ============================================================
  // Bundle creation
  // ============================================================

  it('keeps submit button disabled when summary is empty', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Create Bundle')).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole('button', { name: /generate bundle/i });
    expect(submitBtn).toBeDisabled();
  });

  it('enables submit button when all required fields are filled', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Create Bundle')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('Short issue summary'), { target: { value: 'Something broke badly' } });

    // Fill repro steps and expected/actual behavior
    const textareas = screen.getAllByRole('textbox');
    // summary is input, repro/expected/actual are textareas
    const reproSteps = textareas.find(t => t.tagName === 'TEXTAREA');
    if (reproSteps) {
      fireEvent.change(reproSteps, { target: { value: 'Step 1: Deploy lab\nStep 2: Check logs' } });
    }
    // Fill all textareas
    const allTextareas = screen.getAllByRole('textbox').filter(t => t.tagName === 'TEXTAREA');
    allTextareas.forEach((ta, idx) => {
      if (idx === 0) fireEvent.change(ta, { target: { value: 'Step 1: Deploy lab\nStep 2: Observe failure' } });
      if (idx === 1) fireEvent.change(ta, { target: { value: 'Lab should deploy successfully' } });
      if (idx === 2) fireEvent.change(ta, { target: { value: 'Lab deployment fails with timeout' } });
    });

    await waitFor(() => {
      const submitBtn = screen.getByRole('button', { name: /generate bundle/i });
      expect(submitBtn).not.toBeDisabled();
    });
  });

  it('submits bundle creation and shows status', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Create Bundle')).toBeInTheDocument();
    });

    // Fill required fields
    fireEvent.change(screen.getByPlaceholderText('Short issue summary'), { target: { value: 'Bug report: connectivity issue' } });
    const allTextareas = screen.getAllByRole('textbox').filter(t => t.tagName === 'TEXTAREA');
    allTextareas.forEach((ta, idx) => {
      if (idx === 0) fireEvent.change(ta, { target: { value: 'Step 1: Deploy lab' } });
      if (idx === 1) fireEvent.change(ta, { target: { value: 'Expected result' } });
      if (idx === 2) fireEvent.change(ta, { target: { value: 'Actual result' } });
    });

    await waitFor(() => {
      const submitBtn = screen.getByRole('button', { name: /generate bundle/i });
      expect(submitBtn).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole('button', { name: /generate bundle/i }));

    await waitFor(() => {
      expect(mockCreateSupportBundle).toHaveBeenCalledWith(
        expect.objectContaining({
          summary: 'Bug report: connectivity issue',
          pii_safe: true,
        })
      );
    });
  });

  it('shows checkbox options for labs and agents', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Test Lab')).toBeInTheDocument();
      expect(screen.getByText('Agent 1')).toBeInTheDocument();
    });
  });

  it('renders include configs checkbox', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText(/include raw config snapshots/i)).toBeInTheDocument();
    });
  });

  // ============================================================
  // History
  // ============================================================

  it('renders history table with bundle entries', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Recent Bundles (7 Days)')).toBeInTheDocument();
      expect(screen.getByText('completed')).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
      expect(screen.getByText('running')).toBeInTheDocument();
    });
  });

  it('renders download button for completed bundles', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Download')).toBeInTheDocument();
    });
  });

  it('renders error message for failed bundles', async () => {
    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(screen.getByText('Agent unreachable')).toBeInTheDocument();
    });
  });

  // ============================================================
  // Permissions
  // ============================================================

  it('redirects non-admin users to home', async () => {
    mockCanManageUsers = false;

    render(<SupportBundlesPage />);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true });
    });
  });
});
