import { describe, it, expect } from "vitest";
import {
  builtInThemes,
  DEFAULT_THEME_ID,
  getBuiltInTheme,
  sageStoneTheme,
  oceanTheme,
  copperTheme,
  violetTheme,
  roseTheme,
} from "./presets";

describe("theme/presets", () => {
  describe("builtInThemes", () => {
    it("contains expected number of themes", () => {
      expect(builtInThemes.length).toBe(5);
    });

    it("contains all named themes", () => {
      const themeIds = builtInThemes.map((t) => t.id);
      expect(themeIds).toContain("sage-stone");
      expect(themeIds).toContain("ocean");
      expect(themeIds).toContain("copper");
      expect(themeIds).toContain("violet");
      expect(themeIds).toContain("rose");
    });

    it("all themes have required properties", () => {
      for (const theme of builtInThemes) {
        expect(theme.id).toBeDefined();
        expect(theme.name).toBeDefined();
        expect(theme.colors).toBeDefined();
        expect(theme.colors.accent).toBeDefined();
        expect(theme.colors.neutral).toBeDefined();
        expect(theme.light).toBeDefined();
        expect(theme.dark).toBeDefined();
      }
    });

    it("all themes have complete color scales", () => {
      const requiredShades = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
      for (const theme of builtInThemes) {
        for (const shade of requiredShades) {
          expect(theme.colors.accent[shade as keyof typeof theme.colors.accent]).toBeDefined();
          expect(theme.colors.neutral[shade as keyof typeof theme.colors.neutral]).toBeDefined();
        }
      }
    });
  });

  describe("DEFAULT_THEME_ID", () => {
    it("is sage-stone", () => {
      expect(DEFAULT_THEME_ID).toBe("sage-stone");
    });

    it("corresponds to a valid theme", () => {
      const defaultTheme = getBuiltInTheme(DEFAULT_THEME_ID);
      expect(defaultTheme).toBeDefined();
    });
  });

  describe("getBuiltInTheme", () => {
    it("returns sage-stone theme", () => {
      const theme = getBuiltInTheme("sage-stone");
      expect(theme).toBeDefined();
      expect(theme?.name).toBe("Sage & Stone");
    });

    it("returns ocean theme", () => {
      const theme = getBuiltInTheme("ocean");
      expect(theme).toBeDefined();
      expect(theme?.name).toBe("Ocean");
    });

    it("returns copper theme", () => {
      const theme = getBuiltInTheme("copper");
      expect(theme).toBeDefined();
      expect(theme?.name).toBe("Copper");
    });

    it("returns violet theme", () => {
      const theme = getBuiltInTheme("violet");
      expect(theme).toBeDefined();
      expect(theme?.name).toBe("Violet");
    });

    it("returns rose theme", () => {
      const theme = getBuiltInTheme("rose");
      expect(theme).toBeDefined();
      expect(theme?.name).toBe("Rose");
    });

    it("returns undefined for unknown theme id", () => {
      const theme = getBuiltInTheme("nonexistent");
      expect(theme).toBeUndefined();
    });
  });

  describe("individual theme exports", () => {
    it("sageStoneTheme has correct id", () => {
      expect(sageStoneTheme.id).toBe("sage-stone");
    });

    it("oceanTheme has correct id", () => {
      expect(oceanTheme.id).toBe("ocean");
    });

    it("copperTheme has correct id", () => {
      expect(copperTheme.id).toBe("copper");
    });

    it("violetTheme has correct id", () => {
      expect(violetTheme.id).toBe("violet");
    });

    it("roseTheme has correct id", () => {
      expect(roseTheme.id).toBe("rose");
    });
  });
});
