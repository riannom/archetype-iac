import { describe, it, expect } from "vitest";
import {
  getResourceLevel,
  getResourceBgColor,
  getCpuColor,
  getMemoryColor,
  getStorageColor,
  getRuntimeStatusColor,
  getConnectionStatusColor,
  getConnectionStatusText,
  getRoleBadgeColor,
  getRoleLabel,
  defaultThresholds,
} from "./status";

describe("Resource Level Functions", () => {
  describe("getResourceLevel", () => {
    it("returns normal for values below warning threshold", () => {
      expect(getResourceLevel(30, defaultThresholds.cpu)).toBe("normal");
      expect(getResourceLevel(59, defaultThresholds.cpu)).toBe("normal");
    });

    it("returns warning for values at/above warning threshold", () => {
      expect(getResourceLevel(60, defaultThresholds.cpu)).toBe("warning");
      expect(getResourceLevel(79, defaultThresholds.cpu)).toBe("warning");
    });

    it("returns danger for values at/above danger threshold", () => {
      expect(getResourceLevel(80, defaultThresholds.cpu)).toBe("danger");
      expect(getResourceLevel(100, defaultThresholds.cpu)).toBe("danger");
    });

    it("uses memory thresholds correctly", () => {
      expect(getResourceLevel(60, defaultThresholds.memory)).toBe("normal");
      expect(getResourceLevel(70, defaultThresholds.memory)).toBe("warning");
      expect(getResourceLevel(85, defaultThresholds.memory)).toBe("danger");
    });

    it("uses storage thresholds correctly", () => {
      expect(getResourceLevel(70, defaultThresholds.storage)).toBe("normal");
      expect(getResourceLevel(75, defaultThresholds.storage)).toBe("warning");
      expect(getResourceLevel(90, defaultThresholds.storage)).toBe("danger");
    });
  });

  describe("getResourceBgColor", () => {
    it("returns green for normal level", () => {
      expect(getResourceBgColor("normal")).toBe("bg-green-500");
    });

    it("returns amber for warning level", () => {
      expect(getResourceBgColor("warning")).toBe("bg-amber-500");
    });

    it("returns red for danger level", () => {
      expect(getResourceBgColor("danger")).toBe("bg-red-500");
    });
  });
});

describe("Specific Resource Colors", () => {
  describe("getCpuColor", () => {
    it("returns sage color for normal CPU usage", () => {
      expect(getCpuColor(30)).toBe("bg-sage-500");
    });

    it("returns amber for warning CPU usage", () => {
      expect(getCpuColor(70)).toBe("bg-amber-500");
    });

    it("returns red for danger CPU usage", () => {
      expect(getCpuColor(90)).toBe("bg-red-500");
    });
  });

  describe("getMemoryColor", () => {
    it("returns blue for normal memory usage", () => {
      expect(getMemoryColor(50)).toBe("bg-blue-500");
    });

    it("returns amber for warning memory usage", () => {
      expect(getMemoryColor(75)).toBe("bg-amber-500");
    });

    it("returns red for danger memory usage", () => {
      expect(getMemoryColor(90)).toBe("bg-red-500");
    });
  });

  describe("getStorageColor", () => {
    it("returns violet for normal storage usage", () => {
      expect(getStorageColor(50)).toBe("bg-violet-500");
    });

    it("returns amber for warning storage usage", () => {
      expect(getStorageColor(80)).toBe("bg-amber-500");
    });

    it("returns red for danger storage usage", () => {
      expect(getStorageColor(95)).toBe("bg-red-500");
    });
  });
});

describe("Runtime Status Functions", () => {
  describe("getRuntimeStatusColor", () => {
    it("returns green classes for running status", () => {
      const color = getRuntimeStatusColor("running");
      expect(color).toContain("green");
    });

    it("returns stone classes for stopped status", () => {
      const color = getRuntimeStatusColor("stopped");
      expect(color).toContain("stone");
    });

    it("returns amber classes for pending status", () => {
      const color = getRuntimeStatusColor("pending");
      expect(color).toContain("amber");
    });

    it("returns red classes for error status", () => {
      const color = getRuntimeStatusColor("error");
      expect(color).toContain("red");
    });

    it("returns blue classes for partial status", () => {
      const color = getRuntimeStatusColor("partial");
      expect(color).toContain("blue");
    });

    it("returns stone classes for unknown status", () => {
      const color = getRuntimeStatusColor("unknown");
      expect(color).toContain("stone");
    });
  });
});

describe("Connection Status Functions", () => {
  describe("getConnectionStatusColor", () => {
    it("returns green for online status", () => {
      expect(getConnectionStatusColor("online")).toBe("bg-green-500");
    });

    it("returns red for offline status", () => {
      expect(getConnectionStatusColor("offline")).toBe("bg-red-500");
    });

    it("returns amber for degraded status", () => {
      expect(getConnectionStatusColor("degraded")).toBe("bg-amber-500");
    });

    it("returns blue for connecting status", () => {
      expect(getConnectionStatusColor("connecting")).toBe("bg-blue-500");
    });
  });

  describe("getConnectionStatusText", () => {
    it("returns proper labels for all statuses", () => {
      expect(getConnectionStatusText("online")).toBe("Online");
      expect(getConnectionStatusText("offline")).toBe("Offline");
      expect(getConnectionStatusText("degraded")).toBe("Degraded");
      expect(getConnectionStatusText("connecting")).toBe("Connecting");
    });
  });
});

describe("Role Badge Functions", () => {
  describe("getRoleBadgeColor", () => {
    it("returns purple classes for controller role", () => {
      const color = getRoleBadgeColor("controller");
      expect(color).toContain("purple");
    });

    it("returns blue classes for agent+controller role", () => {
      const color = getRoleBadgeColor("agent+controller");
      expect(color).toContain("blue");
    });

    it("returns sage classes for agent role", () => {
      const color = getRoleBadgeColor("agent");
      expect(color).toContain("sage");
    });
  });

  describe("getRoleLabel", () => {
    it("returns proper labels for all roles", () => {
      expect(getRoleLabel("controller")).toBe("Controller");
      expect(getRoleLabel("agent+controller")).toBe("Agent + Controller");
      expect(getRoleLabel("agent")).toBe("Agent");
    });
  });
});
