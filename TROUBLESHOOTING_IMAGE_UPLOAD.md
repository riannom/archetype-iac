# Image Upload Progress Troubleshooting

## Problem
EOS container image uploads get stuck at 60% progress even though the image successfully imports into Docker.

## What We've Tried

### 1. Initial Issue: Missing Image Error Messages
- Added pre-deployment image validation in `agent/providers/containerlab.py`
- Added verbose error output with stdout/stderr in task logs
- Added `_validate_images()`, `_format_missing_images_error()`, `_parse_deploy_error()` methods

### 2. SSE Streaming Approach (Failed)
- Added streaming endpoint `POST /images/load?stream=true` in `api/app/routers/images.py`
- Used `StreamingResponse` with `text/event-stream` media type
- Frontend parsed SSE events in `web/src/studio/components/DeviceManager.tsx`
- **Problem**: SSE events weren't being received by frontend - stuck at 60%
- Tried:
  - Reading file content before returning StreamingResponse (file was closing)
  - Using async subprocess (`asyncio.create_subprocess_exec`)
  - Adding `await asyncio.sleep(0)` for event loop yielding
  - Adding explicit `\n` after events
- **Result**: Server logs showed 200 OK but frontend never got completion event

### 3. Polling Approach (Current - Partially Working)
- Added background processing with `?background=true` parameter
- New endpoint `GET /images/load/{upload_id}/progress` for polling
- Progress stored in `_upload_progress` dict with threading lock
- Frontend polls every 500ms for updates
- **Current State**:
  - Phases 1-3 work (saving, decompressing, detecting)
  - Phase 4 (docker import) starts but never completes logging
  - subprocess.run appears to hang in background thread

### 4. Subprocess Fixes Attempted
- Added timeout=600 to subprocess.run
- Switched to subprocess.Popen with communicate()
- Added flush=True to print statements
- Added explicit exception handling

## Current Code State

### Backend (`api/app/routers/images.py`)
- `_upload_progress` dict with `_upload_lock` for thread-safe progress tracking
- `_update_progress()` helper function
- `_load_image_background()` runs in daemon thread with phases:
  1. Save file to temp
  2. Decompress if .xz
  3. Detect if docker image or filesystem
  4. Run docker import/load
  5. Update manifest
- Extensive print logging with `[UPLOAD {id}]` prefix

### Frontend (`web/src/studio/components/DeviceManager.tsx`)
- `uploadImageWithPolling()` - uploads with `?background=true`, polls for progress
- `parseErrorMessage()` - extracts meaningful text from HTML error pages
- Polls `/images/load/{upload_id}/progress` every 500ms

### Nginx (`web/nginx.conf`)
- Timeouts increased to 900s (15 min)
- `proxy_buffering off` for streaming

## Log Output From Last Test
```
[UPLOAD e344c53c] Starting background processing for cEOS64-lab-4.35.1F.tar.xz
[UPLOAD e344c53c] Phase 1: Saving file
[UPLOAD e344c53c] File saved: 618407612 bytes, checking if decompression needed
[UPLOAD e344c53c] Filename: cEOS64-lab-4.35.1F.tar.xz, checking for .xz extension
[UPLOAD e344c53c] Phase 2: Decompressing XZ archive
[UPLOAD e344c53c] Decompression complete: 2635806720 bytes
[UPLOAD e344c53c] Phase 3: Detecting format of /tmp/tmp390ydsvr.tar
[UPLOAD e344c53c] Is docker image: False
[UPLOAD e344c53c] Phase 4: Importing as ceos64-lab-4.35.1f:imported
```
Note: Never saw "docker import returned" or "Waiting for docker import" logs

## Hypothesis
The subprocess.Popen or subprocess.run is blocking/hanging when called from a daemon thread in Python. The docker import actually completes (image appears in `docker images`) but the subprocess call never returns to Python.

## Next Steps to Try
1. Check if Python threading + subprocess has known issues
2. Try using `concurrent.futures.ThreadPoolExecutor` instead of raw threading
3. Try running docker import via shell (`subprocess.Popen(['sh', '-c', 'docker import ...'])`)
4. Add non-blocking I/O or use select() to read subprocess output
5. Consider using a proper task queue (RQ/Celery) instead of threads

## Files Modified
- `agent/providers/containerlab.py` - deploy error handling
- `api/app/routers/images.py` - streaming/polling upload
- `web/src/studio/components/DeviceManager.tsx` - frontend upload handling
- `web/nginx.conf` - timeout increases

## Reference: Sanctuary Implementation
Looked at https://github.com/nekoguntai/sanctuary for similar implementation.
They use WebSocket + webhook callback pattern where:
- AI proxy POSTs progress updates back to backend
- Backend broadcasts via WebSocket to frontend
- Uses NDJSON streaming for reading Ollama responses
