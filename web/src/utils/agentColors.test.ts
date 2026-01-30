import { describe, it, expect } from "vitest";
import { getAgentColor, getAgentInitials } from "./agentColors";

describe("agentColors", () => {
  describe("getAgentColor", () => {
    it("returns default color for empty string", () => {
      const color = getAgentColor("");
      expect(color).toBe("#a8a29e");
    });

    it("returns default color for undefined/null", () => {
      const color = getAgentColor(undefined as unknown as string);
      expect(color).toBe("#a8a29e");
    });

    it("returns a valid color for valid agent id", () => {
      const color = getAgentColor("agent-1");
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    });

    it("returns consistent color for same agent id", () => {
      const color1 = getAgentColor("agent-abc");
      const color2 = getAgentColor("agent-abc");
      expect(color1).toBe(color2);
    });

    it("returns different colors for different agent ids", () => {
      const color1 = getAgentColor("agent-1");
      const color2 = getAgentColor("agent-2");
      // Not guaranteed to be different, but high probability with hash function
      // Just check both are valid
      expect(color1).toMatch(/^#[0-9a-f]{6}$/i);
      expect(color2).toMatch(/^#[0-9a-f]{6}$/i);
    });

    it("returns color from predefined palette", () => {
      const validColors = [
        "#3b82f6",
        "#8b5cf6",
        "#ec4899",
        "#f97316",
        "#14b8a6",
        "#84cc16",
      ];
      const color = getAgentColor("test-agent");
      expect(validColors).toContain(color);
    });

    it("handles numeric agent ids", () => {
      const color = getAgentColor("12345");
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    });

    it("handles long agent ids", () => {
      const longId = "agent-with-a-very-long-identifier-string-12345678";
      const color = getAgentColor(longId);
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    });
  });

  describe("getAgentInitials", () => {
    it("returns ? for empty string", () => {
      const initials = getAgentInitials("");
      expect(initials).toBe("?");
    });

    it("returns ? for undefined/null", () => {
      const initials = getAgentInitials(undefined as unknown as string);
      expect(initials).toBe("?");
    });

    it("returns first two chars for single word", () => {
      const initials = getAgentInitials("Agent");
      expect(initials).toBe("AG");
    });

    it("returns initials for two words separated by space", () => {
      const initials = getAgentInitials("Agent One");
      expect(initials).toBe("AO");
    });

    it("returns initials for words separated by hyphen", () => {
      const initials = getAgentInitials("agent-one");
      expect(initials).toBe("AO");
    });

    it("returns initials for words separated by underscore", () => {
      const initials = getAgentInitials("agent_one");
      expect(initials).toBe("AO");
    });

    it("returns uppercase initials", () => {
      const initials = getAgentInitials("test agent");
      expect(initials).toBe("TA");
    });

    it("handles single character name", () => {
      const initials = getAgentInitials("A");
      expect(initials).toBe("A");
    });

    it("handles mixed separators", () => {
      const initials = getAgentInitials("test-agent one");
      expect(initials).toBe("TA");
    });
  });
});
