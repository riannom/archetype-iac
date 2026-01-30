export type ThemeMode = "light" | "dark";

const STORAGE_KEY = "archetype-theme";

export function getStoredTheme(): ThemeMode | null {
  const value = localStorage.getItem(STORAGE_KEY);
  if (value === "light" || value === "dark") {
    return value;
  }
  return null;
}

export function getPreferredTheme(): ThemeMode {
  const stored = getStoredTheme();
  if (stored) {
    return stored;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(mode: ThemeMode) {
  document.documentElement.setAttribute("data-theme", mode);
  localStorage.setItem(STORAGE_KEY, mode);
}

export function toggleTheme(): ThemeMode {
  const current = getPreferredTheme();
  const next = current === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}
