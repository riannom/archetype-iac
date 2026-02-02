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

export interface ToastSettings {
  enabled: boolean;
  position: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
  duration: number;
  showJobStart: boolean;
  showJobComplete: boolean;
  showJobFailed: boolean;
  showJobRetry: boolean;
  showImageSync: boolean;
}

export interface BellSettings {
  enabled: boolean;
  maxHistory: number;
  soundEnabled: boolean;
}

export interface NotificationSettings {
  toasts: ToastSettings;
  bell: BellSettings;
}

export interface CanvasErrorIndicatorSettings {
  showIcon: boolean;
  showBorder: boolean;
  pulseAnimation: boolean;
}

export interface SidebarFilterSettings {
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
}

export interface UserPreferences {
  notification_settings: NotificationSettings;
  canvas_settings: CanvasSettings;
}

export const DEFAULT_TOAST_SETTINGS: ToastSettings = {
  enabled: true,
  position: 'bottom-right',
  duration: 5000,
  showJobStart: true,
  showJobComplete: true,
  showJobFailed: true,
  showJobRetry: true,
  showImageSync: true,
};

export const DEFAULT_BELL_SETTINGS: BellSettings = {
  enabled: true,
  maxHistory: 50,
  soundEnabled: false,
};

export const DEFAULT_NOTIFICATION_SETTINGS: NotificationSettings = {
  toasts: DEFAULT_TOAST_SETTINGS,
  bell: DEFAULT_BELL_SETTINGS,
};

export const DEFAULT_CANVAS_ERROR_SETTINGS: CanvasErrorIndicatorSettings = {
  showIcon: true,
  showBorder: true,
  pulseAnimation: true,
};

export const DEFAULT_SIDEBAR_FILTER_SETTINGS: SidebarFilterSettings = {
  searchQuery: '',
  selectedVendors: [],
  selectedTypes: [],
  imageStatus: 'all',
};

export const DEFAULT_CANVAS_SETTINGS: CanvasSettings = {
  errorIndicator: DEFAULT_CANVAS_ERROR_SETTINGS,
  showAgentIndicators: true,
  sidebarFilters: DEFAULT_SIDEBAR_FILTER_SETTINGS,
  consoleInBottomPanel: false,
};

export const DEFAULT_USER_PREFERENCES: UserPreferences = {
  notification_settings: DEFAULT_NOTIFICATION_SETTINGS,
  canvas_settings: DEFAULT_CANVAS_SETTINGS,
};
