import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "./ThemeProvider";
import { ThemeSelector } from "./ThemeSelector";

function renderWithProvider(isOpen: boolean, onClose = vi.fn()) {
  return render(
    <ThemeProvider>
      <ThemeSelector isOpen={isOpen} onClose={onClose} />
    </ThemeProvider>
  );
}

describe("ThemeSelector", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe("visibility", () => {
    it("renders nothing when closed", () => {
      renderWithProvider(false);

      expect(screen.queryByText("Theme Settings")).not.toBeInTheDocument();
    });

    it("renders modal when open", () => {
      renderWithProvider(true);

      expect(screen.getByText("Theme Settings")).toBeInTheDocument();
    });
  });

  describe("appearance mode", () => {
    it("renders mode buttons", () => {
      renderWithProvider(true);

      expect(screen.getByText("Light")).toBeInTheDocument();
      expect(screen.getByText("Dark")).toBeInTheDocument();
      expect(screen.getByText("System")).toBeInTheDocument();
    });

    it("changes mode when clicking Light button", async () => {
      const user = userEvent.setup();
      renderWithProvider(true);

      await user.click(screen.getByText("Light"));

      expect(document.documentElement.classList.contains("dark")).toBe(false);
    });

    it("changes mode when clicking Dark button", async () => {
      const user = userEvent.setup();
      renderWithProvider(true);

      await user.click(screen.getByText("Dark"));

      expect(document.documentElement.classList.contains("dark")).toBe(true);
    });
  });

  describe("background mode filter", () => {
    it("defaults to light-mode filter in light mode", () => {
      renderWithProvider(true);
      const lightFilterButton = screen.getByRole("button", { name: "Light mode" });
      expect(lightFilterButton.className).toContain("bg-sage-600");
    });

    it("auto-switches mode filter when appearance mode changes", async () => {
      const user = userEvent.setup();
      renderWithProvider(true);

      await user.click(screen.getByText("Dark"));
      const darkFilterButton = screen.getByRole("button", { name: "Dark mode" });
      expect(darkFilterButton.className).toContain("bg-sage-600");
    });
  });

  describe("color themes", () => {
    it("renders built-in theme options", () => {
      renderWithProvider(true);

      expect(screen.getByText("Sage & Stone")).toBeInTheDocument();
      expect(screen.getByText("Ocean")).toBeInTheDocument();
      expect(screen.getByText("Copper")).toBeInTheDocument();
      expect(screen.getByText("Violet")).toBeInTheDocument();
      expect(screen.getByText("Rose")).toBeInTheDocument();
    });

    it("allows selecting a theme", async () => {
      const user = userEvent.setup();
      renderWithProvider(true);

      await user.click(screen.getByText("Ocean"));

      // Check that Ocean theme is now selected (has check icon)
      await waitFor(() => {
        const stored = JSON.parse(localStorage.getItem("archetype_theme_prefs") || "{}");
        expect(stored.themeId).toBe("ocean");
      });
    });

    it("allows favoriting a theme", async () => {
      const user = userEvent.setup();
      renderWithProvider(true);

      const oceanCard = screen.getByText("Ocean").closest("div.relative");
      const favoriteButton = oceanCard?.querySelector('button[title="Add favorite theme"]');
      expect(favoriteButton).toBeTruthy();
      if (!favoriteButton) return;

      await user.click(favoriteButton);

      await waitFor(() => {
        const stored = JSON.parse(localStorage.getItem("archetype_theme_prefs") || "{}");
        expect(stored.favoriteThemeIds).toContain("ocean");
      });
    });
  });

  describe("close behavior", () => {
    it("calls onClose when clicking Done button", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderWithProvider(true, onClose);

      await user.click(screen.getByText("Done"));

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("calls onClose when clicking backdrop", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderWithProvider(true, onClose);

      // Click on the backdrop (parent of modal)
      const backdrop = document.querySelector(".bg-black\\/50");
      if (backdrop) {
        await user.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("calls onClose when clicking close button", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderWithProvider(true, onClose);

      // Find close button by looking for the X icon button in header
      const buttons = screen.getAllByRole("button");
      const closeBtn = buttons.find(
        (btn) => btn.querySelector("i.fa-xmark") !== null
      );
      if (closeBtn) {
        await user.click(closeBtn);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });
  });

  describe("import theme", () => {
    it("renders import button", () => {
      renderWithProvider(true);

      expect(screen.getByText("Import Theme JSON")).toBeInTheDocument();
    });

    it("has hidden file input", () => {
      renderWithProvider(true);

      const fileInput = document.querySelector('input[type="file"]');
      expect(fileInput).toBeInTheDocument();
      expect(fileInput).toHaveClass("hidden");
    });
  });

  describe("accessibility", () => {
    it("has proper heading structure", () => {
      renderWithProvider(true);

      expect(screen.getByRole("heading", { name: "Theme Settings" })).toBeInTheDocument();
    });

    it("mode buttons are accessible", () => {
      renderWithProvider(true);

      const lightButton = screen.getByText("Light");
      expect(lightButton.tagName).toBe("BUTTON");
    });
  });
});
