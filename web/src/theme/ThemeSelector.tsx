import React, { useMemo, useRef, useState, useCallback, useEffect } from 'react';
import { useTheme } from './ThemeProvider';
import type { Theme } from './types';
import { builtInThemes } from './presets';
import {
  backgroundCategories,
  filterBackgroundsByCategory,
  isBackgroundPreferredInMode,
  type BackgroundCategory,
} from './backgrounds';
import { getSuggestedBackgroundForTheme } from './backgroundPairs';

interface ThemeSelectorProps {
  isOpen: boolean;
  onClose: () => void;
}

function ThemePreview({ theme }: { theme: Theme }) {
  return (
    <div className="flex gap-1">
      <div className="w-4 h-4 rounded-sm" style={{ backgroundColor: theme.colors.accent[400] }} />
      <div className="w-4 h-4 rounded-sm" style={{ backgroundColor: theme.colors.accent[600] }} />
      <div className="w-4 h-4 rounded-sm" style={{ backgroundColor: theme.colors.neutral[700] }} />
    </div>
  );
}

function ThemeCard({
  theme,
  iconClass,
  isSelected,
  isFavorite,
  isCustom,
  onSelect,
  onToggleFavorite,
  onExport,
  onRemove,
}: {
  theme: Theme;
  iconClass: string;
  isSelected: boolean;
  isFavorite: boolean;
  isCustom: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  onExport: () => void;
  onRemove?: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={`relative p-3 rounded-lg border-2 cursor-pointer transition-all ${
        isSelected
          ? 'border-sage-500 bg-sage-500/10 dark:bg-sage-500/10'
          : 'border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
      }`}
    >
      <div className="mb-2">
        <div className="flex items-center justify-between gap-2">
          <ThemePreview theme={theme} />
          <i className={`fa-solid ${iconClass} text-stone-500 dark:text-stone-400`} />
        </div>
      </div>
      <div className="text-sm font-medium text-stone-900 dark:text-stone-100">{theme.name}</div>
      {isCustom && <span className="text-xs text-stone-500 dark:text-stone-400">Custom</span>}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onToggleFavorite();
        }}
        className={`absolute top-2 right-2 text-xs ${isFavorite ? 'text-rose-500' : 'text-stone-400'}`}
        title={isFavorite ? 'Remove favorite theme' : 'Add favorite theme'}
      >
        <i className={`${isFavorite ? 'fa-solid' : 'fa-regular'} fa-heart`} />
      </button>
      {isSelected && <i className="fa-solid fa-check absolute top-2 right-7 text-sage-500 text-sm" />}
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
    backgroundId,
    backgroundOpacity,
    taskLogOpacity,
    effectiveMode,
    preferences,
    availableThemes,
    availableBackgrounds,
    setTheme,
    setBackground,
    setBackgroundOpacity,
    setTaskLogOpacity,
    toggleFavoriteBackground,
    toggleFavoriteTheme,
    setMode,
    importTheme,
    exportTheme,
    removeCustomTheme,
  } = useTheme();

  const [importError, setImportError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<BackgroundCategory | 'favorites'>('all');
  const [modeFilter, setModeFilter] = useState<'all' | 'light' | 'dark'>(effectiveMode);
  const [animationFilter, setAnimationFilter] = useState<'all' | 'animated' | 'static'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const favoriteSet = useMemo(() => new Set(preferences.favoriteBackgrounds || []), [preferences.favoriteBackgrounds]);
  const favoriteThemeSet = useMemo(() => new Set(preferences.favoriteThemeIds || []), [preferences.favoriteThemeIds]);
  const suggestedBackgroundId = useMemo(
    () => getSuggestedBackgroundForTheme(theme.id, effectiveMode),
    [theme.id, effectiveMode]
  );

  useEffect(() => {
    setModeFilter(effectiveMode);
  }, [effectiveMode]);

  const getThemeIconClass = (themeId: string) => {
    const icons: Record<string, string> = {
      'sage-stone': 'fa-seedling',
      'ocean': 'fa-water',
      'copper': 'fa-fire',
      'violet': 'fa-gem',
      'rose': 'fa-heart',
      'serenity': 'fa-water',
      'forest': 'fa-tree',
      'cyber': 'fa-microchip',
      'sunrise': 'fa-sun',
      'sunset': 'fa-cloud-sun',
      'sakura-yoshino': 'fa-leaf',
      'sakura-sumie': 'fa-pen',
      'midnight': 'fa-moon',
      'desert': 'fa-mountain',
      'seasonal': 'fa-calendar-days',
    };
    return icons[themeId] || 'fa-palette';
  };

  const getBackgroundIconClass = (bg: { id: string; categories: string[]; animated: boolean }) => {
    if (bg.id === 'none') return 'fa-ban';
    if (bg.animated) return 'fa-sparkles';
    if (bg.categories.includes('weather')) return 'fa-cloud-rain';
    if (bg.categories.includes('water')) return 'fa-water';
    if (bg.categories.includes('sky')) return 'fa-star';
    if (bg.categories.includes('landscape')) return 'fa-mountain';
    if (bg.categories.includes('creatures')) return 'fa-dove';
    if (bg.categories.includes('nature')) return 'fa-leaf';
    if (bg.categories.includes('geometric')) return 'fa-draw-polygon';
    if (bg.categories.includes('zen')) return 'fa-circle-notch';
    return 'fa-image';
  };

  const handleExport = useCallback((themeId: string) => {
    const json = exportTheme(themeId);
    if (!json) return;
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${themeId}-theme.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [exportTheme]);

  const handleImportClick = () => fileInputRef.current?.click();

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
    reader.onerror = () => setImportError('Failed to read file.');
    reader.readAsText(file);
    e.target.value = '';
  }, [importTheme, setTheme]);

  const customThemes = useMemo(
    () => availableThemes.filter(t => !builtInThemes.some(bt => bt.id === t.id)),
    [availableThemes]
  );

  const filteredBackgrounds = useMemo(() => {
    const inCategory = activeCategory === 'favorites'
      ? availableBackgrounds.filter((bg) => favoriteSet.has(bg.id))
      : activeCategory === 'all'
        ? availableBackgrounds
        : filterBackgroundsByCategory(activeCategory).map(pattern => pattern.id)
          .map(id => availableBackgrounds.find(bg => bg.id === id))
          .filter((value): value is NonNullable<typeof value> => Boolean(value));

    const byQuery = !searchQuery.trim()
      ? inCategory
      : inCategory.filter(bg => {
        const query = searchQuery.toLowerCase();
        return bg.name.toLowerCase().includes(query) || bg.id.toLowerCase().includes(query);
      });

    const byModeFilter = byQuery.filter((bg) => {
      if (modeFilter === 'all') return true;
      return isBackgroundPreferredInMode(bg.id, modeFilter);
    });

    const byAnimationFilter = byModeFilter.filter((bg) => {
      if (animationFilter === 'all') return true;
      return animationFilter === 'animated' ? bg.animated : !bg.animated;
    });

    return byAnimationFilter.slice().sort((a, b) => {
      if (a.id === 'none') return -1;
      if (b.id === 'none') return 1;
      const aScore = Number(favoriteSet.has(a.id)) * 2 + Number(a.animated);
      const bScore = Number(favoriteSet.has(b.id)) * 2 + Number(b.animated);
      if (aScore !== bScore) return bScore - aScore;
      return a.name.localeCompare(b.name);
    });
  }, [activeCategory, availableBackgrounds, favoriteSet, searchQuery, modeFilter, animationFilter]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-stone-50 dark:bg-stone-900 rounded-xl shadow-2xl w-full max-w-5xl mx-4 max-h-[92vh] overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-200 dark:border-stone-700">
          <h2 className="text-lg font-semibold text-stone-900 dark:text-stone-100">Theme Settings</h2>
          <button
            onClick={onClose}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
          >
            <i className="fa-solid fa-xmark text-lg" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto max-h-[calc(92vh-8rem)] space-y-8">
          <section>
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">Appearance Mode</h3>
            <div className="flex gap-2">
              {(['light', 'dark', 'system'] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setMode(mode)}
                  className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    preferences.mode === mode
                      ? 'bg-sage-600 text-white'
                      : 'bg-stone-200 dark:bg-stone-700 text-stone-700 dark:text-stone-300 hover:bg-stone-300 dark:hover:bg-stone-600'
                  }`}
                >
                  {mode === 'light' && <i className="fa-solid fa-sun mr-2" />}
                  {mode === 'dark' && <i className="fa-solid fa-moon mr-2" />}
                  {mode === 'system' && <i className="fa-solid fa-desktop mr-2" />}
                  {mode.charAt(0).toUpperCase() + mode.slice(1)}
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">Color Theme</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {builtInThemes.map((t) => (
                <ThemeCard
                  key={t.id}
                  theme={t}
                  iconClass={getThemeIconClass(t.id)}
                  isSelected={theme.id === t.id}
                  isFavorite={favoriteThemeSet.has(t.id)}
                  isCustom={false}
                  onSelect={() => setTheme(t.id)}
                  onToggleFavorite={() => toggleFavoriteTheme(t.id)}
                  onExport={() => handleExport(t.id)}
                />
              ))}
            </div>
          </section>

          {customThemes.length > 0 && (
            <section>
              <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">Custom Themes</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {customThemes.map((t) => (
                  <ThemeCard
                    key={t.id}
                    theme={t}
                    iconClass={getThemeIconClass(t.id)}
                    isSelected={theme.id === t.id}
                    isFavorite={favoriteThemeSet.has(t.id)}
                    isCustom={true}
                    onSelect={() => setTheme(t.id)}
                    onToggleFavorite={() => toggleFavoriteTheme(t.id)}
                    onExport={() => handleExport(t.id)}
                    onRemove={() => removeCustomTheme(t.id)}
                  />
                ))}
              </div>
            </section>
          )}

          <section>
            <div className="flex items-center justify-between mb-3 gap-3">
              <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 uppercase tracking-wide">Background</h3>
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search backgrounds"
                className="px-3 py-1.5 rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-sm text-stone-900 dark:text-stone-100"
              />
            </div>
            <div className="flex flex-wrap gap-2 mb-3">
              {(['all', 'light', 'dark'] as const).map((filterMode) => (
                <button
                  key={filterMode}
                  onClick={() => setModeFilter(filterMode)}
                  className={`px-3 py-1 rounded-full text-xs border ${
                    modeFilter === filterMode
                      ? 'bg-sage-600 text-white border-sage-600'
                      : 'bg-stone-200 dark:bg-stone-800 border-stone-300 dark:border-stone-700 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  {filterMode === 'all' ? 'All modes' : `${filterMode === 'dark' ? 'Dark' : 'Light'} mode`}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2 mb-3">
              {(['all', 'animated', 'static'] as const).map((filterAnimation) => (
                <button
                  key={filterAnimation}
                  onClick={() => setAnimationFilter(filterAnimation)}
                  className={`px-3 py-1 rounded-full text-xs border ${
                    animationFilter === filterAnimation
                      ? 'bg-sage-600 text-white border-sage-600'
                      : 'bg-stone-200 dark:bg-stone-800 border-stone-300 dark:border-stone-700 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  {filterAnimation === 'all' ? 'All types' : filterAnimation === 'animated' ? 'Animated' : 'Static'}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2 mb-3">
              <button
                onClick={() => setActiveCategory('favorites')}
                className={`px-3 py-1 rounded-full text-xs border ${
                  activeCategory === 'favorites'
                    ? 'bg-sage-600 text-white border-sage-600'
                    : 'bg-stone-200 dark:bg-stone-800 border-stone-300 dark:border-stone-700 text-stone-700 dark:text-stone-300'
                }`}
              >
                Favorites ({favoriteSet.size})
              </button>
              {backgroundCategories.map(category => (
                <button
                  key={category.id}
                  onClick={() => setActiveCategory(category.id)}
                  className={`px-3 py-1 rounded-full text-xs border ${
                    activeCategory === category.id
                      ? 'bg-sage-600 text-white border-sage-600'
                      : 'bg-stone-200 dark:bg-stone-800 border-stone-300 dark:border-stone-700 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  {category.label} ({filterBackgroundsByCategory(category.id).length})
                </button>
              ))}
            </div>
            <div className="mb-3 text-xs text-stone-500 dark:text-stone-400">
              Backgrounds that do not match current {effectiveMode} mode are visible but disabled.
            </div>
            {backgroundId !== suggestedBackgroundId && (
              <div className="mb-3 text-xs text-stone-600 dark:text-stone-300">
                Suggested for {theme.name}:{" "}
                <button
                  onClick={() => setBackground(suggestedBackgroundId)}
                  className="text-sage-600 dark:text-sage-400 hover:underline"
                >
                  {suggestedBackgroundId}
                </button>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3 max-h-[18rem] overflow-y-auto pr-2">
              {filteredBackgrounds.map((bg) => {
                const isSelected = backgroundId === bg.id;
                const isFavorite = favoriteSet.has(bg.id);
                const isSelectableInCurrentMode = isBackgroundPreferredInMode(bg.id, effectiveMode);
                return (
                  <div
                    key={bg.id}
                    onClick={() => {
                      if (!isSelectableInCurrentMode) return;
                      setBackground(bg.id);
                    }}
                    className={`relative rounded-lg p-3 border text-left transition-all cursor-pointer ${
                      isSelected
                        ? 'border-sage-500 bg-sage-500/10'
                        : 'border-stone-200 dark:border-stone-700 hover:border-sage-400'
                    } ${
                      isSelectableInCurrentMode ? '' : 'opacity-45 cursor-not-allowed'
                    }`}
                    title={!isSelectableInCurrentMode ? `Available in ${bg.preferredMode} mode` : undefined}
                  >
                    <div className="absolute left-2 top-2 text-stone-400">
                      <i className={`fa-solid ${getBackgroundIconClass(bg)} text-xs`} />
                    </div>
                    <div className="pl-4 text-sm font-medium text-stone-900 dark:text-stone-100 truncate">
                      {bg.name}
                    </div>
                    <div className="mt-1 pl-4 text-xs text-stone-500 dark:text-stone-400 truncate">{bg.id}</div>
                    <div className="mt-2 flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
                      <span title={bg.animated ? 'Animated background' : 'Static background'}>
                        <i className={`fa-solid ${bg.animated ? 'fa-sparkles' : 'fa-image'}`} />
                      </span>
                      {bg.preferredMode && bg.preferredMode !== 'both' && (
                        <span className="px-1.5 py-0.5 rounded bg-stone-200 dark:bg-stone-700">
                          {bg.preferredMode === 'dark' ? 'Dark only' : 'Light only'}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleFavoriteBackground(bg.id);
                      }}
                      className={`absolute top-2 right-2 text-xs ${isFavorite ? 'text-rose-500' : 'text-stone-400'}`}
                      title={isFavorite ? 'Remove favorite' : 'Add favorite'}
                    >
                      <i className={`${isFavorite ? 'fa-solid' : 'fa-regular'} fa-heart`} />
                    </button>
                    {isSelected && (
                      <i className="fa-solid fa-check absolute bottom-2 right-2 text-sage-500 text-xs" />
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-4">
              <label className="block text-sm text-stone-700 dark:text-stone-300 mb-2">
                Background Opacity: {backgroundOpacity}%
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={backgroundOpacity}
                onChange={(event) => setBackgroundOpacity(parseInt(event.target.value, 10))}
                className="w-full"
              />
            </div>
            <div className="mt-4">
              <label className="block text-sm text-stone-700 dark:text-stone-300 mb-2">
                Task Log Opacity: {taskLogOpacity}%
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={taskLogOpacity}
                onChange={(event) => setTaskLogOpacity(parseInt(event.target.value, 10))}
                className="w-full"
              />
            </div>
          </section>

          <section>
            <h3 className="text-sm font-medium text-stone-700 dark:text-stone-300 mb-3 uppercase tracking-wide">Import Custom Theme</h3>
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
            {importError && <p className="mt-2 text-sm text-red-500">{importError}</p>}
          </section>
        </div>

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
