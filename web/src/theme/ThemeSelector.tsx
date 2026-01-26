import React, { useState, useRef, useCallback } from 'react';
import { useTheme } from './ThemeProvider';
import type { Theme } from './types';
import { builtInThemes } from './presets';

interface ThemeSelectorProps {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Render a preview of a theme's colors
 */
function ThemePreview({ theme, isSelected }: { theme: Theme; isSelected: boolean }) {
  return (
    <div className="flex gap-1">
      {/* Accent colors preview */}
      <div
        className="w-4 h-4 rounded-sm"
        style={{ backgroundColor: theme.colors.accent[400] }}
      />
      <div
        className="w-4 h-4 rounded-sm"
        style={{ backgroundColor: theme.colors.accent[600] }}
      />
      <div
        className="w-4 h-4 rounded-sm"
        style={{ backgroundColor: theme.colors.neutral[700] }}
      />
    </div>
  );
}

/**
 * Theme card for selection grid
 */
function ThemeCard({
  theme,
  isSelected,
  isCustom,
  onSelect,
  onExport,
  onRemove,
}: {
  theme: Theme;
  isSelected: boolean;
  isCustom: boolean;
  onSelect: () => void;
  onExport: () => void;
  onRemove?: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={`
        relative p-3 rounded-lg border-2 cursor-pointer transition-all
        ${isSelected
          ? 'border-sage-500 bg-sage-500/10 dark:bg-sage-500/10'
          : 'border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
        }
      `}
    >
      {/* Theme preview colors */}
      <div className="mb-2">
        <ThemePreview theme={theme} isSelected={isSelected} />
      </div>

      {/* Theme name */}
      <div className="text-sm font-medium text-stone-900 dark:text-stone-100">
        {theme.name}
      </div>

      {/* Custom badge */}
      {isCustom && (
        <span className="text-xs text-stone-500 dark:text-stone-400">Custom</span>
      )}

      {/* Selected indicator */}
      {isSelected && (
        <div className="absolute top-2 right-2">
          <i className="fa-solid fa-check text-sage-500 text-sm" />
        </div>
      )}

      {/* Action buttons */}
      <div className="absolute bottom-2 right-2 flex gap-1 opacity-0 hover:opacity-100 transition-opacity">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onExport();
          }}
          className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300"
          title="Export theme"
        >
          <i className="fa-solid fa-download text-xs" />
        </button>
        {isCustom && onRemove && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="p-1 text-stone-400 hover:text-red-500"
            title="Remove theme"
          >
            <i className="fa-solid fa-trash text-xs" />
          </button>
        )}
      </div>
    </div>
  );
}

export function ThemeSelector({ isOpen, onClose }: ThemeSelectorProps) {
  const {
    theme,
    preferences,
    availableThemes,
    setTheme,
    setMode,
    importTheme,
    exportTheme,
    removeCustomTheme,
  } = useTheme();

  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Handle mode selection
  const handleModeChange = (mode: 'light' | 'dark' | 'system') => {
    setMode(mode);
  };

  // Handle theme selection
  const handleThemeSelect = (themeId: string) => {
    setTheme(themeId);
  };

  // Handle export
  const handleExport = useCallback((themeId: string) => {
    const json = exportTheme(themeId);
    if (json) {
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${themeId}-theme.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  }, [exportTheme]);

  // Handle import
  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const content = event.target?.result as string;
      const imported = importTheme(content);
      if (imported) {
        setImportError(null);
        setTheme(imported.id);
      } else {
        setImportError('Invalid theme file. Please check the format.');
      }
    };
    reader.onerror = () => {
      setImportError('Failed to read file.');
    };
    reader.readAsText(file);

    // Reset input so same file can be selected again
    e.target.value = '';
  }, [importTheme, setTheme]);

  // Handle remove custom theme
  const handleRemove = useCallback((themeId: string) => {
    removeCustomTheme(themeId);
  }, [removeCustomTheme]);

  if (!isOpen) return null;

  const customThemes = availableThemes.filter(
    t => !builtInThemes.some(bt => bt.id === t.id)
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-stone-50 dark:bg-stone-900 rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-200 dark:border-stone-700">
          <h2 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
            Theme Settings
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
          >
            <i className="fa-solid fa-xmark text-lg" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto max-h-[calc(90vh-8rem)]">
          {/* Appearance Mode */}
          <div className="mb-6">
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">
              Appearance Mode
            </h3>
            <div className="flex gap-2">
              {(['light', 'dark', 'system'] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => handleModeChange(mode)}
                  className={`
                    flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-all
                    ${preferences.mode === mode
                      ? 'bg-sage-600 text-white'
                      : 'bg-stone-200 dark:bg-stone-700 text-stone-700 dark:text-stone-300 hover:bg-stone-300 dark:hover:bg-stone-600'
                    }
                  `}
                >
                  {mode === 'light' && <i className="fa-solid fa-sun mr-2" />}
                  {mode === 'dark' && <i className="fa-solid fa-moon mr-2" />}
                  {mode === 'system' && <i className="fa-solid fa-desktop mr-2" />}
                  {mode.charAt(0).toUpperCase() + mode.slice(1)}
                </button>
              ))}
            </div>
          </div>

          {/* Color Theme */}
          <div className="mb-6">
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">
              Color Theme
            </h3>
            <div className="grid grid-cols-2 gap-3">
              {builtInThemes.map((t) => (
                <ThemeCard
                  key={t.id}
                  theme={t}
                  isSelected={theme.id === t.id}
                  isCustom={false}
                  onSelect={() => handleThemeSelect(t.id)}
                  onExport={() => handleExport(t.id)}
                />
              ))}
            </div>
          </div>

          {/* Custom Themes */}
          {customThemes.length > 0 && (
            <div className="mb-6">
              <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">
                Custom Themes
              </h3>
              <div className="grid grid-cols-2 gap-3">
                {customThemes.map((t) => (
                  <ThemeCard
                    key={t.id}
                    theme={t}
                    isSelected={theme.id === t.id}
                    isCustom={true}
                    onSelect={() => handleThemeSelect(t.id)}
                    onExport={() => handleExport(t.id)}
                    onRemove={() => handleRemove(t.id)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Import Custom Theme */}
          <div>
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">
              Import Custom Theme
            </h3>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleFileChange}
              className="hidden"
            />
            <button
              onClick={handleImportClick}
              className="w-full px-4 py-3 border-2 border-dashed border-stone-300 dark:border-stone-600 rounded-lg text-stone-600 dark:text-stone-400 hover:border-sage-500 hover:text-sage-600 dark:hover:text-sage-400 transition-colors"
            >
              <i className="fa-solid fa-upload mr-2" />
              Import Theme JSON
            </button>
            {importError && (
              <p className="mt-2 text-sm text-red-500">{importError}</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-700 flex justify-end">
          <button
            onClick={onClose}
            className="px-6 py-2 bg-sage-600 hover:bg-sage-700 text-white rounded-lg font-medium transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
