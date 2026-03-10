# Simplification Plan: Animations, StudioPage, Metrics

## Overview

Three areas targeted for simplification:

| Area | Current State | Target | Estimated Reduction |
|------|--------------|--------|-------------------|
| Canvas background animations | 56 individual hooks, no shared abstraction | Shared `useCanvasAnimation` hook | ~1,500 lines |
| StudioPage.tsx | 1,295 lines, 46 state vars, 34 handlers | ~500-600 lines of orchestration | ~700 lines |
| Backend metrics | 1,478 lines (988 + 337 + 153), dead code | ~900 lines | ~550 lines |

---

## Part 1: Canvas Background Animation Consolidation

### Current State
- 56 animation hooks in `web/src/components/backgrounds/animations/`
- Each follows identical boilerplate: `useEffect` → canvas setup → `requestAnimationFrame` loop → cleanup
- `AnimatedBackground.tsx` imports all 56, activates one at a time
- No `useCanvasAnimation` hook exists yet

### The Hook: `useCanvasAnimation`

Extract shared boilerplate into a single hook:

```typescript
// web/src/components/backgrounds/hooks/useCanvasAnimation.ts
interface UseCanvasAnimationOptions {
  canvasRef: RefObject<HTMLCanvasElement>;
  enabled: boolean;
  darkMode: boolean;
  opacity: number;
  draw: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, time: number, dt: number) => void;
  init?: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void;
  cleanup?: () => void;
}

function useCanvasAnimation(options: UseCanvasAnimationOptions): void
```

This abstracts:
- Canvas context acquisition + DPR scaling
- RAF loop with delta time calculation
- Resize observer + canvas dimension sync
- Cleanup on unmount / disable
- Early-exit when `enabled=false`

### Approach

**Phase 1A: Create hook + migrate 5 simple animations** (proof of concept)
- [ ] Create `useCanvasAnimation` hook with full boilerplate abstraction
- [ ] Migrate 5 simple particle-system animations (useFireflies, useSnowfall, useSakuraPetals, useFallingLeaves, useRaindrops)
- [ ] Verify no visual regression

**Phase 1B: Batch migrate remaining 51 animations** (mechanical)
- [ ] Migrate nature/zen animations (useLotusBloom, useMoonlitClouds, useTidePools, etc.)
- [ ] Migrate abstract animations (useBreath, useMyceliumNetwork, useOilSlick, etc.)
- [ ] Migrate complex scene animations (usePaperBoats, useConstellation, useThunderstorm, etc.)
- [ ] Each migration: replace useEffect/RAF boilerplate with `useCanvasAnimation({ draw: ... })`

**Phase 1C: Cleanup**
- [ ] Remove duplicated resize/DPR logic from all 56 files
- [ ] Verify all animations still work via manual check

### Risk
- Low: purely cosmetic, no functional impact
- Each animation is self-contained — can migrate incrementally

---

## Part 2: StudioPage.tsx Decomposition

### Current State (1,295 lines)
- 46 state variables
- 34 event handlers (~450 lines)
- 7 view types in `renderView()` (~200 lines)
- Tab navigation UI (~75 lines)
- 4 modal state groups scattered throughout
- 7 custom hooks already extracted (good foundation)

### Decomposition Plan

**Phase 2A: Extract modal state management** (~120 lines saved)
- [ ] `useConfigViewerModal()` — 3 state vars + 2 handlers + modal JSX trigger
- [ ] `useJobLogModal()` — 2 state vars + 2 handlers
- [ ] `useTaskLogEntryModal()` — 2 state vars + 2 handlers
- [ ] `useYamlExportModal()` — 2 state vars + export handler

**Phase 2B: Extract `<TabNavigation>` component** (~75 lines saved)
- [ ] New component: `web/src/studio/components/TabNavigation.tsx`
- [ ] Props: `view`, `onViewChange`, `hasAgents`, `isDesignerView`
- [ ] Moves the 7-tab rendering block out of StudioPage

**Phase 2C: Extract complex handler hooks** (~200 lines saved)
- [ ] `useNodeStatusControl()` — `handleUpdateStatus` (lines 430-511): pending ops tracking, optimistic updates, error recovery
- [ ] `useLabExport()` — `handleExport`, `handleDownloadBundle`, `handleExportFull` (lines 817-857)
- [ ] `useAddDevice()` — `handleAddDevice`, `handleAddExternalNetwork` (lines 350-401)
- [ ] `useScenarioHighlights()` — scenario highlight derivation (lines 175-209)

**Phase 2D: Extract auth flow** (~70 lines saved)
- [ ] `useStudioAuth()` — auth state, studioRequest wrapper, login handler, isUnauthorized check
- [ ] Consolidates 3 scattered state vars + 2 functions + conditional render logic

**Phase 2E: Extract lab entry sync** (~35 lines saved)
- [ ] `useLabEntrySync()` — deterministic first-load sequence (lines 220-244)
- [ ] Complex async flow worth isolating for testability

### Dependency Order
2A → 2B → 2C → 2D → 2E (each phase independently testable, but this order minimizes merge conflicts)

### Target
StudioPage becomes ~500-600 lines of pure orchestration: hook composition, prop threading, and layout rendering.

---

## Part 3: Backend Metrics Simplification

### Current State (1,478 lines total)

| File | Lines | Purpose |
|------|-------|---------|
| `api/app/metrics.py` | 988 | 44 metric definitions + recording functions + bulk updaters |
| `api/app/services/metrics_service.py` | 337 | Dashboard aggregation (DEAD CODE) |
| `api/app/timing.py` | 153 | @TimedOperation decorator + helpers |

### Simplification Steps

**Phase 3A: Delete dead code** (~380 lines saved)
- [ ] Delete `api/app/services/metrics_service.py` entirely (337 lines, never imported)
- [ ] Remove 5 unused metrics from `metrics.py`: `agent_cpu_percent`, `agent_memory_percent`, `agent_containers_running`, `agent_vms_running`, `agent_stale_image_cleanup_total` (~40 lines)
- [ ] Remove corresponding `update_agent_metrics()` population code for deleted metrics

**Phase 3B: Eliminate DummyMetric duplication** (~80 lines saved)
- [ ] Replace 44 individual DummyMetric fallback assignments with a factory:
  ```python
  def _make_metric(cls, name, desc, labels=None):
      if HAS_PROMETHEUS:
          return cls(name, desc, labels or [])
      return DummyMetric()
  ```
- [ ] Each metric becomes one line instead of two (definition + fallback)

**Phase 3C: Consolidate label normalization** (~15 lines saved)
- [ ] Remove redundant double-normalization in recording functions
- [ ] Single normalization pass at the recording boundary

**Phase 3D: Inline circuit breaker metric** (~10 lines saved)
- [ ] Move `circuit_breaker_state` gauge definition to `cleanup_handler.py` (sole consumer)
- [ ] Remove from global metrics export

### What NOT to simplify
- `timing.py` (153 lines) — the `@TimedOperation` decorator is well-designed, pairs metrics with structured logging, used by 8 callers
- `infer_job_failure_reason()` — 40+ pattern matches are valuable for root cause analysis
- Graceful degradation pattern — prometheus_client optional is a good design choice

### Verification
- [ ] `python3 -c "import ast; ast.parse(open('api/app/metrics.py').read())"` after each phase
- [ ] Grep for all removed imports to ensure no broken references
- [ ] `/metrics` endpoint still returns valid Prometheus format

---

## Execution Order

Recommended order by independence and risk:

1. **Part 3A** (delete dead metrics code) — zero risk, immediate 380-line win
2. **Part 1A** (create useCanvasAnimation hook + 5 migrations) — low risk, proof of concept
3. **Part 2A-2B** (modals + tab nav extraction) — mechanical, low risk
4. **Part 3B** (DummyMetric factory) — contained refactor
5. **Part 1B** (batch migrate remaining animations) — mechanical, time-consuming
6. **Part 2C-2E** (complex handler extraction) — higher complexity, needs careful testing
7. **Part 3C-3D** (label normalization, inline metric) — small wins

Total estimated reduction: **~2,750 lines** across all three areas.
