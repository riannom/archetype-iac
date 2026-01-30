import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import {
  StatusIndicator,
  StatusIndicatorStatus,
  StatusIndicatorSize,
} from "./StatusIndicator";

describe("StatusIndicator", () => {
  describe("rendering", () => {
    it("renders status indicator", () => {
      render(<StatusIndicator status="online" />);
      const indicator = document.querySelector(".rounded-full");
      expect(indicator).toBeInTheDocument();
    });
  });

  describe("statuses", () => {
    const statuses: StatusIndicatorStatus[] = [
      "online",
      "offline",
      "warning",
      "running",
      "stopped",
      "pending",
      "error",
      "connecting",
    ];

    statuses.forEach((status) => {
      it(`renders ${status} status`, () => {
        render(<StatusIndicator status={status} />);
        const indicator = document.querySelector(".rounded-full");
        expect(indicator).toBeInTheDocument();
      });
    });
  });

  describe("colors", () => {
    it("has green color for online status", () => {
      render(<StatusIndicator status="online" />);
      const indicator = document.querySelector(".bg-green-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has green color for running status", () => {
      render(<StatusIndicator status="running" />);
      const indicator = document.querySelector(".bg-green-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has amber color for warning status", () => {
      render(<StatusIndicator status="warning" />);
      const indicator = document.querySelector(".bg-amber-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has amber color for pending status", () => {
      render(<StatusIndicator status="pending" />);
      const indicator = document.querySelector(".bg-amber-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has red color for offline status", () => {
      render(<StatusIndicator status="offline" />);
      const indicator = document.querySelector(".bg-red-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has red color for error status", () => {
      render(<StatusIndicator status="error" />);
      const indicator = document.querySelector(".bg-red-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has blue color for connecting status", () => {
      render(<StatusIndicator status="connecting" />);
      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has stone color for stopped status", () => {
      render(<StatusIndicator status="stopped" />);
      const indicator = document.querySelector(".bg-stone-400");
      expect(indicator).toBeInTheDocument();
    });
  });

  describe("sizes", () => {
    const sizes: StatusIndicatorSize[] = ["sm", "md", "lg"];

    sizes.forEach((size) => {
      it(`renders ${size} size`, () => {
        render(<StatusIndicator status="online" size={size} />);
        const indicator = document.querySelector(".rounded-full");
        expect(indicator).toBeInTheDocument();
      });
    });

    it("sm size has w-2 h-2 classes", () => {
      render(<StatusIndicator status="online" size="sm" />);
      const indicator = document.querySelector(".w-2.h-2");
      expect(indicator).toBeInTheDocument();
    });

    it("md size has w-3 h-3 classes", () => {
      render(<StatusIndicator status="online" size="md" />);
      const indicator = document.querySelector(".w-3.h-3");
      expect(indicator).toBeInTheDocument();
    });

    it("lg size has w-4 h-4 classes", () => {
      render(<StatusIndicator status="online" size="lg" />);
      const indicator = document.querySelector(".w-4.h-4");
      expect(indicator).toBeInTheDocument();
    });

    it("uses md size by default", () => {
      render(<StatusIndicator status="online" />);
      const indicator = document.querySelector(".w-3.h-3");
      expect(indicator).toBeInTheDocument();
    });
  });

  describe("pulse animation", () => {
    it("pulses by default for online status", () => {
      render(<StatusIndicator status="online" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).toBeInTheDocument();
    });

    it("pulses by default for running status", () => {
      render(<StatusIndicator status="running" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).toBeInTheDocument();
    });

    it("pulses by default for pending status", () => {
      render(<StatusIndicator status="pending" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).toBeInTheDocument();
    });

    it("pulses by default for connecting status", () => {
      render(<StatusIndicator status="connecting" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).toBeInTheDocument();
    });

    it("does not pulse by default for offline status", () => {
      render(<StatusIndicator status="offline" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).not.toBeInTheDocument();
    });

    it("does not pulse by default for stopped status", () => {
      render(<StatusIndicator status="stopped" />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).not.toBeInTheDocument();
    });

    it("can force pulse on", () => {
      render(<StatusIndicator status="offline" pulse />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).toBeInTheDocument();
    });

    it("can force pulse off", () => {
      render(<StatusIndicator status="online" pulse={false} />);
      const indicator = document.querySelector(".animate-pulse");
      expect(indicator).not.toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<StatusIndicator status="online" className="custom-indicator" />);
      const indicator = document.querySelector(".custom-indicator");
      expect(indicator).toBeInTheDocument();
    });

    it("has rounded-full class", () => {
      render(<StatusIndicator status="online" />);
      const indicator = document.querySelector(".rounded-full");
      expect(indicator).toBeInTheDocument();
    });
  });
});
