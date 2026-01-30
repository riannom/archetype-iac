import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { DEFAULT_THEME_ID } from "./presets";

// Test component that uses theme context
function TestConsumer() {
  const {
    theme,
    mode,
    effectiveMode,
    preferences,
    availableThemes,
    setTheme,
    setMode,
    toggleMode,
    importTheme,
    exportTheme,
    removeCustomTheme,
  } = useTheme();

  return (
    <div>
      <span data-testid="theme-id">{theme.id}</span>
      <span data-testid="theme-name">{theme.name}</span>
      <span data-testid="mode">{mode}</span>
      <span data-testid="effective-mode">{effectiveMode}</span>
      <span data-testid="pref-theme-id">{preferences.themeId}</span>
      <span data-testid="pref-mode">{preferences.mode}</span>
      <span data-testid="theme-count">{availableThemes.length}</span>
      <button onClick={() => setTheme("ocean")} data-testid="set-ocean">
        Set Ocean
      </button>
      <button onClick={() => setMode("light")} data-testid="set-light">
        Set Light
      </button>
      <button onClick={() => setMode("dark")} data-testid="set-dark">
        Set Dark
      </button>
      <button onClick={() => setMode("system")} data-testid="set-system">
        Set System
      </button>
      <button onClick={toggleMode} data-testid="toggle">
        Toggle
      </button>
      <button
        onClick={() => {
          const json = exportTheme(theme.id);
          if (json) {
            const span = document.getElementById("export-result");
            if (span) span.textContent = json;
          }
        }}
        data-testid="export"
      >
        Export
      </button>
      <span id="export-result" data-testid="export-result"></span>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    document.documentElement.removeAttribute("style");
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe("initial state", () => {
    it("provides default theme", () => {
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      expect(screen.getByTestId("theme-id")).toHaveTextContent(DEFAULT_THEME_ID);
    });

    it("provides all built-in themes", () => {
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      expect(screen.getByTestId("theme-count")).toHaveTextContent("5");
    });

    it("loads stored preferences", () => {
      localStorage.setItem(
        "archetype_theme_prefs",
        JSON.stringify({ themeId: "ocean", mode: "light" })
      );

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      expect(screen.getByTestId("theme-id")).toHaveTextContent("ocean");
      expect(screen.getByTestId("pref-mode")).toHaveTextContent("light");
    });

    it("uses default preferences for invalid stored data", () => {
      localStorage.setItem("archetype_theme_prefs", "invalid json");

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      expect(screen.getByTestId("theme-id")).toHaveTextContent(DEFAULT_THEME_ID);
    });
  });

  describe("setTheme", () => {
    it("changes current theme", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-ocean"));

      expect(screen.getByTestId("theme-id")).toHaveTextContent("ocean");
      expect(screen.getByTestId("theme-name")).toHaveTextContent("Ocean");
    });

    it("persists theme preference to localStorage", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-ocean"));

      const stored = JSON.parse(localStorage.getItem("archetype_theme_prefs") || "{}");
      expect(stored.themeId).toBe("ocean");
    });
  });

  describe("setMode", () => {
    it("changes to light mode", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-light"));

      expect(screen.getByTestId("mode")).toHaveTextContent("light");
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("light");
    });

    it("changes to dark mode", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-dark"));

      expect(screen.getByTestId("mode")).toHaveTextContent("dark");
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("dark");
    });

    it("changes to system mode", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-system"));

      // System mode uses matchMedia which is mocked to return matches: false (prefers light)
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("light");
    });

    it("persists mode preference to localStorage", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-light"));

      const stored = JSON.parse(localStorage.getItem("archetype_theme_prefs") || "{}");
      expect(stored.mode).toBe("light");
    });
  });

  describe("toggleMode", () => {
    it("toggles from dark to light", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      // Set to dark first
      await user.click(screen.getByTestId("set-dark"));
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("dark");

      // Toggle to light
      await user.click(screen.getByTestId("toggle"));
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("light");
    });

    it("toggles from light to dark", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      // Set to light first
      await user.click(screen.getByTestId("set-light"));
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("light");

      // Toggle to dark
      await user.click(screen.getByTestId("toggle"));
      expect(screen.getByTestId("effective-mode")).toHaveTextContent("dark");
    });
  });

  describe("exportTheme", () => {
    it("exports theme as JSON string", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("export"));

      const exported = screen.getByTestId("export-result").textContent;
      expect(exported).toBeTruthy();
      const parsed = JSON.parse(exported || "{}");
      expect(parsed.id).toBe(DEFAULT_THEME_ID);
    });
  });

  describe("DOM application", () => {
    it("applies dark class for dark mode", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-dark"));

      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });

    it("removes dark class for light mode", async () => {
      const user = userEvent.setup();

      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      await user.click(screen.getByTestId("set-light"));

      expect(document.documentElement.classList.contains("dark")).toBe(false);
    });

    it("sets CSS custom properties for accent colors", async () => {
      render(
        <ThemeProvider>
          <TestConsumer />
        </ThemeProvider>
      );

      // Wait for effect to run
      await waitFor(() => {
        const accentColor = document.documentElement.style.getPropertyValue("--color-accent-500");
        expect(accentColor).toBeTruthy();
      });
    });
  });

  describe("error handling", () => {
    it("throws error when useTheme used outside provider", () => {
      // Suppress console.error for this test
      const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      expect(() => {
        render(<TestConsumer />);
      }).toThrow("useTheme must be used within a ThemeProvider");

      consoleSpy.mockRestore();
    });
  });
});
