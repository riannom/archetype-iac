import React, { useState, useRef, useEffect } from 'react';
import { useTheme, ThemeSelector } from '../../theme/index';
import { ArchetypeIcon } from '../../components/icons';
import { NotificationCenter } from '../../components/NotificationCenter';
import { NotificationSettingsPanel } from '../../components/NotificationSettingsPanel';

interface TopBarProps {
  labName: string;
  onExport: () => void;
  onExportFull?: () => void;
  onExit: () => void;
  onRename?: (newName: string) => void;
}

const TopBar: React.FC<TopBarProps> = ({ labName, onExport, onExportFull, onExit, onRename }) => {
  const { effectiveMode, toggleMode } = useTheme();
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const [showExportDropdown, setShowExportDropdown] = useState(false);
  const [showNotificationSettings, setShowNotificationSettings] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(labName);
  const exportDropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync editName when labName changes
  useEffect(() => {
    setEditName(labName);
  }, [labName]);

  // Focus input when editing starts
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (exportDropdownRef.current && !exportDropdownRef.current.contains(event.target as Node)) {
        setShowExportDropdown(false);
      }
    };
    if (showExportDropdown) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showExportDropdown]);

  const handleSaveEdit = () => {
    const trimmed = editName.trim();
    if (trimmed && trimmed !== labName && onRename) {
      onRename(trimmed);
    } else {
      setEditName(labName);
    }
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveEdit();
    } else if (e.key === 'Escape') {
      setEditName(labName);
      setIsEditing(false);
    }
  };

  return (
    <>
      <div className="h-14 bg-white/40 dark:bg-black/40 backdrop-blur-xl border-b border-stone-200/70 dark:border-black/70 flex items-center justify-between px-6 z-20 shadow-sm shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={onExit}
            className="w-8 h-8 flex items-center justify-center text-stone-400 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-black/70 rounded-lg transition-all"
            title="Back to Dashboard"
          >
            <i className="fa-solid fa-chevron-left"></i>
          </button>

          <div className="flex items-center gap-3 group cursor-default">
            <ArchetypeIcon size={32} className="text-sage-600 dark:text-sage-400 group-hover:text-sage-500 dark:group-hover:text-sage-300 transition-colors" />
            <div className="flex flex-col leading-none">
              <span className="text-stone-900 dark:text-white font-black text-lg tracking-tighter">
                ARCHETYPE
              </span>
              <span className="text-[9px] text-sage-600 dark:text-sage-500 font-bold tracking-[0.2em] uppercase">
                Network Studio
              </span>
            </div>
          </div>

          <div className="h-8 w-px bg-stone-200 dark:bg-black/80 mx-2"></div>

          <div className="flex items-center gap-2 px-3 py-1 bg-stone-100 dark:bg-black/60 rounded-full border border-stone-200/50 dark:border-black/70">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
            <span className="text-[11px] text-stone-500 dark:text-stone-400 font-medium uppercase tracking-tight">Lab:</span>
            {isEditing ? (
              <input
                ref={inputRef}
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                onBlur={handleSaveEdit}
                onKeyDown={handleKeyDown}
                className="text-xs font-semibold text-stone-700 dark:text-stone-100 bg-transparent border-b border-sage-500 outline-none w-32"
              />
            ) : (
              <button
                onClick={() => onRename && setIsEditing(true)}
                className="text-xs font-semibold text-stone-700 dark:text-stone-100 hover:text-sage-600 dark:hover:text-sage-400 transition-colors cursor-pointer flex items-center gap-1 group"
                title={onRename ? "Click to rename" : undefined}
              >
                {labName}
                {onRename && <i className="fa-solid fa-pencil text-[8px] opacity-0 group-hover:opacity-50 transition-opacity"></i>}
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-4">
          <NotificationCenter />

          <button
            onClick={() => setShowNotificationSettings(true)}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-black/65 text-stone-600 dark:text-stone-300 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border border-stone-200 dark:border-black/70"
            title="Notification Settings"
          >
            <i className="fa-solid fa-gear"></i>
          </button>

          <button
            onClick={() => setShowThemeSelector(true)}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-black/65 text-stone-600 dark:text-stone-300 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border border-stone-200 dark:border-black/70"
            title="Theme Settings"
          >
            <i className="fa-solid fa-palette"></i>
          </button>

          <button
            onClick={toggleMode}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-black/65 text-stone-600 dark:text-stone-300 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border border-stone-200 dark:border-black/70"
            title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
          >
            <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'}`}></i>
          </button>

          <div className="relative" ref={exportDropdownRef}>
            <button
              onClick={() => setShowExportDropdown(!showExportDropdown)}
              className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-black/65 hover:bg-stone-50 dark:hover:bg-black/80 text-stone-700 dark:text-stone-100 text-xs font-semibold border border-stone-200 dark:border-black/70 rounded-lg transition-all active:scale-95 shadow-sm"
            >
              <i className="fa-solid fa-file-code text-sage-600 dark:text-sage-400"></i>
              EXPORT
              <i className={`fa-solid fa-chevron-down text-[8px] transition-transform ${showExportDropdown ? 'rotate-180' : ''}`}></i>
            </button>
            {showExportDropdown && (
              <div className="absolute right-0 mt-1 w-48 bg-white dark:bg-stone-950 border border-stone-200 dark:border-black/80 rounded-lg shadow-lg overflow-hidden z-50">
                <button
                  onClick={() => {
                    onExport();
                    setShowExportDropdown(false);
                  }}
                  className="w-full px-3 py-2 text-left text-xs text-stone-700 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-black/80 flex items-center gap-2"
                >
                  <i className="fa-solid fa-file-code text-sage-600 dark:text-sage-400 w-4"></i>
                  Export YAML
                  <span className="text-[9px] text-stone-400 dark:text-stone-400 ml-auto">IAC only</span>
                </button>
                {onExportFull && (
                  <button
                    onClick={() => {
                      onExportFull();
                      setShowExportDropdown(false);
                    }}
                    className="w-full px-3 py-2 text-left text-xs text-stone-700 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-black/80 flex items-center gap-2 border-t border-stone-100 dark:border-black/70"
                  >
                    <i className="fa-solid fa-file-zipper text-blue-600 dark:text-blue-400 w-4"></i>
                    Export Full
                    <span className="text-[9px] text-stone-400 dark:text-stone-400 ml-auto">+ Layout</span>
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="h-8 w-px bg-stone-200 dark:bg-black/80 mx-1"></div>

          <button
            onClick={onExit}
            className="flex items-center gap-2 px-3 py-1.5 text-stone-500 hover:text-red-500 dark:text-stone-400 dark:hover:text-red-400 text-xs font-bold transition-all"
            title="Logout"
          >
            <i className="fa-solid fa-right-from-bracket"></i>
            LOGOUT
          </button>
        </div>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />

      <NotificationSettingsPanel
        isOpen={showNotificationSettings}
        onClose={() => setShowNotificationSettings(false)}
      />
    </>
  );
};

export default TopBar;
