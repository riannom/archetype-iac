import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getStoredTheme,
  getPreferredTheme,
  applyTheme,
  toggleTheme,
} from "./theme";

describe("theme", () => {
  beforeEach(() => {
    localStorage.clear();
    // Reset document state
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe("getStoredTheme", () => {
    it("returns null when no theme stored", () => {
      const result = getStoredTheme();
      expect(result).toBeNull();
    });

    it("returns 'light' when light theme stored", () => {
      localStorage.setItem("archetype-theme", "light");
      const result = getStoredTheme();
      expect(result).toBe("light");
    });

    it("returns 'dark' when dark theme stored", () => {
      localStorage.setItem("archetype-theme", "dark");
      const result = getStoredTheme();
      expect(result).toBe("dark");
    });

    it("returns null for invalid stored value", () => {
      localStorage.setItem("archetype-theme", "invalid");
      const result = getStoredTheme();
      expect(result).toBeNull();
    });
  });

  describe("getPreferredTheme", () => {
    it("returns stored theme when available", () => {
      localStorage.setItem("archetype-theme", "light");
      const result = getPreferredTheme();
      expect(result).toBe("light");
    });

    it("returns light when system prefers light (default mock)", () => {
      // matchMedia is mocked in setupTests.ts to return matches: false (prefers light)
      const result = getPreferredTheme();
      expect(result).toBe("light");
    });
  });

  describe("applyTheme", () => {
    it("sets data-theme attribute on document", () => {
      applyTheme("dark");
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    });

    it("stores theme in localStorage", () => {
      applyTheme("light");
      expect(localStorage.getItem("archetype-theme")).toBe("light");
    });

    it("updates data-theme when called multiple times", () => {
      applyTheme("dark");
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

      applyTheme("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    });
  });

  describe("toggleTheme", () => {
    it("toggles from dark to light", () => {
      localStorage.setItem("archetype-theme", "dark");
      const result = toggleTheme();
      expect(result).toBe("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    });

    it("toggles from light to dark", () => {
      localStorage.setItem("archetype-theme", "light");
      const result = toggleTheme();
      expect(result).toBe("dark");
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    });

    it("stores new theme in localStorage", () => {
      localStorage.setItem("archetype-theme", "light");
      toggleTheme();
      expect(localStorage.getItem("archetype-theme")).toBe("dark");
    });
  });
});
