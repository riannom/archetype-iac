import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArchetypeIcon } from "../components/icons";
import { useTheme } from "../theme";
import { backgroundPatterns } from "../theme/backgrounds";
import { builtInThemes } from "../theme/presets";
import { useUser } from "../contexts/UserContext";
import { canViewInfrastructure } from "../utils/permissions";
import AdminMenuButton from "../components/AdminMenuButton";
import {
  getInfrastructureSettings,
  updateInfrastructureSettings,
  type InfrastructureSettingsUpdate,
} from "../api";

type LoginModeSettings = {
  themeId: string;
  backgroundId: string;
  opacity: number;
};

export default function AdminSettingsPage() {
  const navigate = useNavigate();
  const { user, loading: userLoading } = useUser();
  const { effectiveMode, toggleMode } = useTheme();
  const [darkDefaults, setDarkDefaults] = useState<LoginModeSettings>({
    themeId: "midnight",
    backgroundId: "floating-lanterns",
    opacity: 50,
  });
  const [lightDefaults, setLightDefaults] = useState<LoginModeSettings>({
    themeId: "sakura-sumie",
    backgroundId: "sakura-redux",
    opacity: 100,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const themeOptions = useMemo(
    () => builtInThemes.map((theme) => ({ id: theme.id, name: theme.name })),
    []
  );

  useEffect(() => {
    if (!userLoading && !canViewInfrastructure(user ?? null)) {
      navigate("/", { replace: true });
    }
  }, [navigate, user, userLoading]);

  useEffect(() => {
    const load = async () => {
      try {
        const settings = await getInfrastructureSettings();
        setDarkDefaults({
          themeId: settings.login_dark_theme_id,
          backgroundId: settings.login_dark_background_id,
          opacity: settings.login_dark_background_opacity,
        });
        setLightDefaults({
          themeId: settings.login_light_theme_id,
          backgroundId: settings.login_light_background_id,
          opacity: settings.login_light_background_opacity,
        });
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Failed to load settings");
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  async function handleSave() {
    setSaving(true);
    setStatus(null);
    const payload: InfrastructureSettingsUpdate = {
      login_dark_theme_id: darkDefaults.themeId,
      login_dark_background_id: darkDefaults.backgroundId,
      login_dark_background_opacity: darkDefaults.opacity,
      login_light_theme_id: lightDefaults.themeId,
      login_light_background_id: lightDefaults.backgroundId,
      login_light_background_opacity: lightDefaults.opacity,
    };
    try {
      await updateInfrastructureSettings(payload);
      setStatus("Saved");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] text-stone-700 dark:text-stone-200">
      <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
        <div className="flex items-center gap-4">
          <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
          <div>
            <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ADMIN SETTINGS</h1>
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Global Defaults</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <AdminMenuButton />

          <button
            onClick={toggleMode}
            className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all"
            title={`Switch to ${effectiveMode === "dark" ? "light" : "dark"} mode`}
          >
            <i className={`fa-solid ${effectiveMode === "dark" ? "fa-sun" : "fa-moon"} text-sm`}></i>
          </button>
          <button
            onClick={() => navigate("/")}
            className="flex items-center gap-2 px-3 py-2 glass-control text-stone-700 dark:text-stone-100 rounded-lg transition-all"
            title="Back to workspace"
          >
            <i className="fa-solid fa-arrow-left text-xs"></i>
            <span className="text-[10px] font-bold uppercase">Back</span>
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto p-8 space-y-8">
        <section className="glass-surface-elevated rounded-2xl p-6">
          <h2 className="text-lg font-semibold text-stone-900 dark:text-stone-100 mb-2">Login Screen Defaults</h2>
          <p className="text-sm text-stone-500 dark:text-stone-400 mb-6">
            These values control the login screen appearance based on system light/dark mode.
          </p>

          {loading ? (
            <p className="text-sm text-stone-500 dark:text-stone-400">Loading settings...</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-4">
                <h3 className="text-sm font-bold uppercase tracking-wider text-stone-700 dark:text-stone-200">Dark Mode</h3>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Theme
                  <select
                    className="mt-2 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
                    value={darkDefaults.themeId}
                    onChange={(e) => setDarkDefaults((prev) => ({ ...prev, themeId: e.target.value }))}
                  >
                    {themeOptions.map((opt) => (
                      <option key={opt.id} value={opt.id}>{opt.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Background
                  <select
                    className="mt-2 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
                    value={darkDefaults.backgroundId}
                    onChange={(e) => setDarkDefaults((prev) => ({ ...prev, backgroundId: e.target.value }))}
                  >
                    {backgroundPatterns.map((bg) => (
                      <option key={bg.id} value={bg.id}>{bg.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Background Opacity ({darkDefaults.opacity}%)
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={darkDefaults.opacity}
                    onChange={(e) => setDarkDefaults((prev) => ({ ...prev, opacity: Number(e.target.value) }))}
                    className="mt-3 w-full"
                  />
                </label>
              </div>

              <div className="space-y-4">
                <h3 className="text-sm font-bold uppercase tracking-wider text-stone-700 dark:text-stone-200">Light Mode</h3>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Theme
                  <select
                    className="mt-2 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
                    value={lightDefaults.themeId}
                    onChange={(e) => setLightDefaults((prev) => ({ ...prev, themeId: e.target.value }))}
                  >
                    {themeOptions.map((opt) => (
                      <option key={opt.id} value={opt.id}>{opt.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Background
                  <select
                    className="mt-2 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
                    value={lightDefaults.backgroundId}
                    onChange={(e) => setLightDefaults((prev) => ({ ...prev, backgroundId: e.target.value }))}
                  >
                    {backgroundPatterns.map((bg) => (
                      <option key={bg.id} value={bg.id}>{bg.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs font-semibold uppercase tracking-wider text-stone-500 dark:text-stone-400">
                  Background Opacity ({lightDefaults.opacity}%)
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={lightDefaults.opacity}
                    onChange={(e) => setLightDefaults((prev) => ({ ...prev, opacity: Number(e.target.value) }))}
                    className="mt-3 w-full"
                  />
                </label>
              </div>
            </div>
          )}

          <div className="mt-8 flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={saving || loading}
              className="px-5 py-2.5 rounded-lg bg-sage-600 hover:bg-sage-500 disabled:opacity-60 text-white text-sm font-semibold transition-all"
            >
              {saving ? "Saving..." : "Save Settings"}
            </button>
            {status && <span className="text-sm text-stone-500 dark:text-stone-400">{status}</span>}
          </div>
        </section>
      </main>
    </div>
  );
}
