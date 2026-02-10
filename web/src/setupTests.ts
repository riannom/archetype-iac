import "@testing-library/jest-dom";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { enableMapSet } from "immer";

// Enable immer MapSet plugin globally before any store modules are loaded
enableMapSet();

// Cleanup after each test
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia for components that use media queries
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock ResizeObserver for ReactFlow and other components
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

window.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;

// Mock scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Mock canvas context for animated background tests
HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue({
  clearRect: vi.fn(),
  fillRect: vi.fn(),
  beginPath: vi.fn(),
  closePath: vi.fn(),
  arc: vi.fn(),
  ellipse: vi.fn(),
  fill: vi.fn(),
  stroke: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  bezierCurveTo: vi.fn(),
  quadraticCurveTo: vi.fn(),
  roundRect: vi.fn(),
  save: vi.fn(),
  restore: vi.fn(),
  translate: vi.fn(),
  rotate: vi.fn(),
  scale: vi.fn(),
  setTransform: vi.fn(),
  createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  fillText: vi.fn(),
  strokeText: vi.fn(),
  measureText: vi.fn(() => ({ width: 0 })),
  shadowBlur: 0,
  shadowColor: '',
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
  font: '12px sans-serif',
  globalAlpha: 1,
  lineCap: 'round',
  lineJoin: 'round',
});

const originalConsoleError = console.error;
const originalConsoleWarn = console.warn;

const ignoredErrorPatterns = [
  /not wrapped in act/i,
  /not implemented: navigation/i,
];

const ignoredWarnPatterns = [
  /react router future flag warning/i,
];

console.error = (...args) => {
  const message = args[0] instanceof Error ? args[0].message : String(args[0] ?? '');
  if (ignoredErrorPatterns.some((pattern) => pattern.test(message))) {
    return;
  }
  originalConsoleError(...args);
};

console.warn = (...args) => {
  const message = args[0] instanceof Error ? args[0].message : String(args[0] ?? '');
  if (ignoredWarnPatterns.some((pattern) => pattern.test(message))) {
    return;
  }
  originalConsoleWarn(...args);
};
