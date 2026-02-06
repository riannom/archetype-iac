# On-Demand Image Sync - Test Plan Implementation

## Status: COMPLETE (Tests Only - TDD Phase)

### Completed
- [x] `api/tests/test_on_demand_image_sync.py` — 17 backend tests
- [x] `api/tests/test_on_demand_sync_broadcasting.py` — 4 backend broadcast tests
- [x] `web/src/studio/components/PropertiesPanel.ondemand.test.tsx` — 5 frontend tests
- [x] `web/src/studio/hooks/useLabStateWS.ondemand.test.ts` — 3 frontend tests
- [x] `web/src/components/ImageSyncProgress.ondemand.test.tsx` — 3 frontend tests
- [x] Python syntax verification — both files pass
- [x] TypeScript compilation — `tsc --noEmit` passes
- [x] Frontend test execution — 11/11 pass

### Results
- **32 total tests** across 5 files
- Frontend: 11/11 passing (existing overlay/WS infrastructure works)
- Backend: syntax-verified (need venv with pytest for full execution)

### Next: Implementation
See plan at `.claude/plans/quirky-orbiting-wadler.md` for implementation steps.
Key files to modify:
- `api/app/tasks/jobs.py:1729-1803` — Make image sync non-blocking
- `api/app/tasks/image_sync.py` — Add sync completion callback
- `api/app/routers/images.py:1450-1468` — Trigger node start on completion
