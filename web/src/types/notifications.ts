export type NotificationLevel = 'info' | 'success' | 'warning' | 'error';

export interface Notification {
  id: string;
  level: NotificationLevel;
  title: string;
  message?: string;
  timestamp: Date;
  jobId?: string;
  labId?: string;
  read: boolean;
  category?: string;
  suggestion?: string;
}

interface ToastSettings {
  enabled: boolean;
  position: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
  duration: number;
  showJobStart: boolean;
  showJobComplete: boolean;
  showJobFailed: boolean;
  showJobRetry: boolean;
  showImageSync: boolean;
  showSyncJobs: boolean;
}

interface BellSettings {
  enabled: boolean;
  maxHistory: number;
  soundEnabled: boolean;
  showJobStart: boolean;
  showJobComplete: boolean;
  showJobFailed: boolean;
  showJobRetry: boolean;
  showImageSync: boolean;
  showSyncJobs: boolean;
}

export interface NotificationSettings {
  toasts: ToastSettings;
  bell: BellSettings;
}

interface CanvasErrorIndicatorSettings {
  showIcon: boolean;
  showBorder: boolean;
  pulseAnimation: boolean;
}

interface SidebarFilterSettings {
  searchQuery: string;
  selectedVendors: string[];
  selectedTypes: string[];
  imageStatus: 'all' | 'has_image' | 'has_default' | 'no_image';
}

export interface CanvasSettings {
  errorIndicator: CanvasErrorIndicatorSettings;
  showAgentIndicators: boolean;
  sidebarFilters: SidebarFilterSettings;
  consoleInBottomPanel: boolean;
  metricsBarExpanded: boolean;
}

export interface UserPreferences {
  notification_settings: NotificationSettings;
  canvas_settings: CanvasSettings;
}

const DEFAULT_TOAST_SETTINGS: ToastSettings = {
  enabled: true,
  position: 'bottom-right',
  duration: 5000,
  showJobStart: true,
  showJobComplete: true,
  showJobFailed: true,
  showJobRetry: true,
  showImageSync: true,
  showSyncJobs: false,
};

const DEFAULT_BELL_SETTINGS: BellSettings = {
  enabled: true,
  maxHistory: 50,
  soundEnabled: false,
  showJobStart: false,
  showJobComplete: false,
  showJobFailed: true,
  showJobRetry: false,
  showImageSync: false,
  showSyncJobs: false,
};

const DEFAULT_NOTIFICATION_SETTINGS: NotificationSettings = {
  toasts: DEFAULT_TOAST_SETTINGS,
  bell: DEFAULT_BELL_SETTINGS,
};

const DEFAULT_CANVAS_ERROR_SETTINGS: CanvasErrorIndicatorSettings = {
  showIcon: true,
  showBorder: true,
  pulseAnimation: true,
};

const DEFAULT_SIDEBAR_FILTER_SETTINGS: SidebarFilterSettings = {
  searchQuery: '',
  selectedVendors: [],
  selectedTypes: [],
  imageStatus: 'all',
};

const DEFAULT_CANVAS_SETTINGS: CanvasSettings = {
  errorIndicator: DEFAULT_CANVAS_ERROR_SETTINGS,
  showAgentIndicators: true,
  sidebarFilters: DEFAULT_SIDEBAR_FILTER_SETTINGS,
  consoleInBottomPanel: false,
  metricsBarExpanded: false,
};

export const DEFAULT_USER_PREFERENCES: UserPreferences = {
  notification_settings: DEFAULT_NOTIFICATION_SETTINGS,
  canvas_settings: DEFAULT_CANVAS_SETTINGS,
};
