// ============================================================================
// Badge/Status Helper Functions
// ============================================================================

export const getMtuSyncStatusBadge = (status: string): { color: string; icon: string; text: string } => {
  switch (status) {
    case 'synced':
      return { color: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400', icon: 'fa-check', text: 'Synced' };
    case 'mismatch':
      return { color: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400', icon: 'fa-triangle-exclamation', text: 'Mismatch' };
    case 'error':
      return { color: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400', icon: 'fa-times-circle', text: 'Error' };
    case 'unconfigured':
      return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-minus', text: 'Not Configured' };
    default:
      return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-question', text: 'Unknown' };
  }
};

export const getStatusBadgeStyle = (status: string): string => {
  switch (status) {
    case 'success':
      return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700';
    case 'failed':
      return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700';
    case 'pending':
      return 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700';
    default:
      return 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-300 dark:border-stone-700';
  }
};

export const getPathBadge = (testPath: string): { color: string; label: string } => {
  if (testPath === 'data_plane') {
    return { color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400', label: 'Transport' };
  }
  return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', label: 'Management' };
};

export const getInterfaceTypeBadge = (type: string): { color: string; text: string } => {
  switch (type) {
    case 'transport':
      return { color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400', text: 'Transport' };
    case 'external':
      return { color: 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400', text: 'External' };
    case 'custom':
      return { color: 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400', text: 'Custom' };
    default:
      return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', text: type };
  }
};

export const getManagedIfaceSyncBadge = (status: string): { color: string; icon: string; text: string } => {
  switch (status) {
    case 'synced':
      return { color: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400', icon: 'fa-check', text: 'Synced' };
    case 'mismatch':
      return { color: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400', icon: 'fa-triangle-exclamation', text: 'Mismatch' };
    case 'provisioning':
      return { color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400', icon: 'fa-spinner fa-spin', text: 'Provisioning' };
    case 'error':
      return { color: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400', icon: 'fa-times-circle', text: 'Error' };
    case 'unconfigured':
      return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-minus', text: 'Pending' };
    default:
      return { color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-question', text: status };
  }
};
