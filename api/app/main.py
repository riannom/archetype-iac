"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from app import db, models
from app.db import SessionLocal
from app.config import settings
from app.auth import get_current_user, hash_password
from app.catalog import list_devices as catalog_devices, list_images as catalog_images
from app.logging_config import (
    correlation_id_var,
    generate_correlation_id,
    set_correlation_id,
    setup_logging,
)
from app.middleware import CurrentUserMiddleware
from app.routers import admin, agents, auth, callbacks, console, events, images, iso, jobs, labs, permissions, webhooks
from app.tasks.health import agent_health_monitor
from app.tasks.job_health import job_health_monitor
from app.tasks.reconciliation import state_reconciliation_monitor
from app.tasks.disk_cleanup import disk_cleanup_monitor

# Configure structured logging at module load
setup_logging()

logger = logging.getLogger(__name__)

# Background task handles
_agent_monitor_task: asyncio.Task | None = None
_reconciliation_task: asyncio.Task | None = None
_job_health_task: asyncio.Task | None = None
_disk_cleanup_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start background tasks on startup, cleanup on shutdown."""
    global _agent_monitor_task, _reconciliation_task, _job_health_task, _disk_cleanup_task

    # Startup
    logger.info("Starting Archetype API controller")

    # Create database tables
    logger.info("Creating database tables")
    models.Base.metadata.create_all(bind=db.engine)

    # Seed admin user if configured
    if settings.admin_email and settings.admin_password:
        session = SessionLocal()
        try:
            existing = session.query(models.User).filter(models.User.email == settings.admin_email).first()
            if not existing:
                if len(settings.admin_password.encode("utf-8")) > 72:
                    logger.warning("Skipping admin seed: ADMIN_PASSWORD must be 72 bytes or fewer")
                else:
                    admin_user = models.User(
                        email=settings.admin_email,
                        hashed_password=hash_password(settings.admin_password),
                        is_admin=True,
                    )
                    session.add(admin_user)
                    session.commit()
                    logger.info(f"Created admin user: {settings.admin_email}")
        finally:
            session.close()

    # Start agent health monitor background task
    _agent_monitor_task = asyncio.create_task(agent_health_monitor())

    # Start state reconciliation monitor background task
    _reconciliation_task = asyncio.create_task(state_reconciliation_monitor())

    # Start job health monitor background task
    _job_health_task = asyncio.create_task(job_health_monitor())

    # Start disk cleanup monitor background task
    _disk_cleanup_task = asyncio.create_task(disk_cleanup_monitor())

    yield

    # Shutdown
    logger.info("Shutting down Archetype API controller")

    if _agent_monitor_task:
        _agent_monitor_task.cancel()
        try:
            await _agent_monitor_task
        except asyncio.CancelledError:
            pass

    if _reconciliation_task:
        _reconciliation_task.cancel()
        try:
            await _reconciliation_task
        except asyncio.CancelledError:
            pass

    if _job_health_task:
        _job_health_task.cancel()
        try:
            await _job_health_task
        except asyncio.CancelledError:
            pass

    if _disk_cleanup_task:
        _disk_cleanup_task.cancel()
        try:
            await _disk_cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Archetype API", version="0.1.0", lifespan=lifespan)


# Correlation ID middleware for request tracing
class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation IDs for request tracing.

    Extracts X-Correlation-ID from incoming requests or generates a new one.
    Sets the correlation ID in context for use by loggers throughout the request.
    Returns the correlation ID in the response header.
    """

    async def dispatch(self, request: Request, call_next):
        # Extract correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            correlation_id = generate_correlation_id()

        # Set in context for logging
        token = correlation_id_var.set(correlation_id)

        try:
            response = await call_next(request)
            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            # Reset context
            correlation_id_var.reset(token)


# Middleware (order matters - correlation ID should be early)
app.add_middleware(CorrelationIdMiddleware)
if settings.session_secret:
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CurrentUserMiddleware)

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(labs.router)
app.include_router(jobs.router)
app.include_router(permissions.router)
app.include_router(images.router)
app.include_router(console.router)
app.include_router(admin.router)
app.include_router(callbacks.router)
app.include_router(events.router)
app.include_router(iso.router)
app.include_router(webhooks.router)


# Simple endpoints that remain in main.py
@app.get("/health")
def health(request: Request) -> dict[str, str]:
    user = request.state.user
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "user": user.email if user else "",
    }


@app.get("/devices")
def list_devices() -> dict[str, object]:
    data = catalog_devices()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/images")
def list_images() -> dict[str, object]:
    data = catalog_images()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/vendors")
def list_vendors() -> list[dict]:
    """Return vendor configurations for frontend device catalog.

    This endpoint provides a unified view of all supported network devices,
    including their categories, icons, versions, and availability status.
    Data is sourced from the centralized vendor registry in agent/vendors.py,
    merged with any custom device types defined per installation.
    Hidden devices are filtered out.
    """
    from agent.vendors import get_vendors_for_ui
    from app.image_store import load_custom_devices, load_hidden_devices

    # Get base vendor configs
    result = get_vendors_for_ui()

    # Load hidden device IDs
    hidden_ids = set(load_hidden_devices())

    # Filter out hidden devices from vendor registry
    def filter_models(models: list[dict]) -> list[dict]:
        return [m for m in models if m.get("id") not in hidden_ids]

    for cat_data in result:
        if "subCategories" in cat_data:
            for subcat in cat_data["subCategories"]:
                subcat["models"] = filter_models(subcat.get("models", []))
            # Remove empty subcategories
            cat_data["subCategories"] = [
                s for s in cat_data["subCategories"] if s.get("models")
            ]
        elif "models" in cat_data:
            cat_data["models"] = filter_models(cat_data.get("models", []))

    # Remove empty categories
    result = [c for c in result if c.get("models") or c.get("subCategories")]

    # Load custom devices and merge them (custom devices aren't hidden)
    custom_devices = load_custom_devices()
    if custom_devices:
        # Group custom devices by category
        custom_by_category: dict[str, list[dict]] = {}
        for device in custom_devices:
            cat = device.get("category", "Compute")
            if cat not in custom_by_category:
                custom_by_category[cat] = []
            custom_by_category[cat].append(device)

        # Merge into existing categories or create new ones
        for cat_data in result:
            cat_name = cat_data.get("name")
            if cat_name in custom_by_category:
                # Add to existing category
                if "subCategories" in cat_data:
                    # Find "Custom" subcategory or create one
                    other_subcat = None
                    for subcat in cat_data["subCategories"]:
                        if subcat.get("name") == "Custom":
                            other_subcat = subcat
                            break
                    if other_subcat:
                        other_subcat["models"].extend(custom_by_category[cat_name])
                    else:
                        cat_data["subCategories"].append({
                            "name": "Custom",
                            "models": custom_by_category[cat_name]
                        })
                elif "models" in cat_data:
                    cat_data["models"].extend(custom_by_category[cat_name])
                del custom_by_category[cat_name]

        # Add remaining categories that don't exist
        for cat_name, devices in custom_by_category.items():
            result.append({
                "name": cat_name,
                "models": devices
            })

    return result


@app.post("/vendors")
def add_custom_device(
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Add a custom device type.

    Required fields:
    - id: Unique device identifier
    - name: Display name

    Optional fields:
    - type: Device type (router, switch, firewall, host, container)
    - category: UI category (Network, Security, Compute, Cloud & External)
    - vendor: Vendor name (default: "Custom")
    - icon: FontAwesome icon class (default: "fa-box")
    - versions: List of versions (default: ["latest"])
    - memory: Memory requirement in MB (default: 1024)
    - cpu: CPU cores required (default: 1)
    - maxPorts: Maximum interfaces (default: 8)
    - portNaming: Interface naming pattern (default: "eth")
    - portStartIndex: Starting port number (default: 0)
    - requiresImage: Whether user must provide image (default: true)
    - supportedImageKinds: List of image types (default: ["docker"])
    - licenseRequired: Whether license is required (default: false)
    - documentationUrl: Link to documentation
    - tags: Searchable tags
    """
    from app.image_store import add_custom_device as store_add_device, find_custom_device
    from agent.vendors import VENDOR_CONFIGS

    device_id = payload.get("id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Device ID is required")
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="Device name is required")

    # Check if device ID conflicts with vendor registry
    if device_id in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=409,
            detail=f"Device ID '{device_id}' conflicts with built-in vendor registry"
        )

    # Check if already exists as custom device
    if find_custom_device(device_id):
        raise HTTPException(
            status_code=409,
            detail=f"Custom device '{device_id}' already exists"
        )

    try:
        device = store_add_device(payload)
        return {"device": device}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/vendors/{device_id}")
def delete_device(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete or hide a device type.

    - Custom devices: Permanently deleted (only if no images assigned)
    - Built-in devices: Hidden from the UI (can be restored later)

    Both require no images to be assigned to the device.
    Accepts device IDs or aliases (e.g., 'eos' or 'ceos' both work for Arista EOS).
    """
    from app.image_store import (
        find_custom_device,
        delete_custom_device as store_delete_device,
        get_device_image_count,
        hide_device,
        is_device_hidden,
    )
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID (for vendor registry lookup)
    canonical_id = get_kind_for_device(device_id)

    # Check if any images are assigned to this device (check both original and canonical)
    image_count = get_device_image_count(device_id)
    if canonical_id != device_id:
        image_count += get_device_image_count(canonical_id)
    if image_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete device with {image_count} assigned image(s). Unassign images first."
        )

    # Check if it's a built-in vendor device (use canonical ID)
    if canonical_id in VENDOR_CONFIGS:
        # Check if already hidden (we hide by the device_id used in UI, which is the alias)
        if is_device_hidden(device_id):
            raise HTTPException(
                status_code=400,
                detail=f"Device '{device_id}' is already hidden"
            )
        # Hide instead of delete - use the device_id as passed (the alias shown in UI)
        hide_device(device_id)
        return {"message": f"Built-in device '{device_id}' hidden successfully"}

    # Check if custom device exists
    device = find_custom_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    deleted = store_delete_device(device_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")

    return {"message": f"Custom device '{device_id}' deleted successfully"}


@app.post("/vendors/{device_id}/restore")
def restore_hidden_device(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Restore a hidden built-in device type.

    Only built-in devices that have been hidden can be restored.
    Accepts device IDs or aliases.
    """
    from app.image_store import unhide_device, is_device_hidden
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Check if it's a built-in vendor device
    if canonical_id not in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail="Only built-in devices can be restored"
        )

    # Check if it's actually hidden
    if not is_device_hidden(device_id):
        raise HTTPException(
            status_code=400,
            detail=f"Device '{device_id}' is not hidden"
        )

    unhide_device(device_id)
    return {"message": f"Device '{device_id}' restored successfully"}


@app.get("/vendors/hidden")
def list_hidden_devices(
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """List all hidden built-in devices."""
    from app.image_store import load_hidden_devices
    return {"hidden": load_hidden_devices()}


@app.put("/vendors/{device_id}")
def update_custom_device_endpoint(
    device_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Update a custom device type's properties.

    Body can include any of:
    - name: Display name
    - category: UI category
    - vendor: Vendor name
    - icon: FontAwesome icon class
    - versions: List of versions
    - memory: Memory requirement in MB
    - cpu: CPU cores required
    - maxPorts: Maximum interfaces
    - portNaming: Interface naming pattern
    - requiresImage: Whether user must provide image
    - supportedImageKinds: List of image types
    - licenseRequired: Whether license is required
    - documentationUrl: Link to docs
    - tags: Searchable tags
    - isActive: Whether device is available in UI
    """
    from app.image_store import find_custom_device, update_custom_device
    from agent.vendors import VENDOR_CONFIGS

    # Check if it's a built-in vendor device
    if device_id in VENDOR_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify built-in vendor devices"
        )

    # Check if custom device exists
    if not find_custom_device(device_id):
        raise HTTPException(status_code=404, detail="Custom device not found")

    updated = update_custom_device(device_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")

    return {"device": updated}


@app.get("/vendors/{device_id}/config")
def get_device_config(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Get full device configuration including base config, overrides, and effective values.

    Returns:
        - base: The base configuration from vendor registry or custom device
        - overrides: User-defined configuration overrides
        - effective: Merged configuration (base + overrides)
    """
    from app.image_store import (
        find_custom_device,
        get_device_override,
    )
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device, _get_vendor_options

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    base_config = {}

    # Check if it's a built-in vendor device
    if canonical_id in VENDOR_CONFIGS:
        config = VENDOR_CONFIGS[canonical_id]
        base_config = {
            "id": device_id,
            "kind": config.kind,
            "vendor": config.vendor,
            "name": config.label or config.vendor,
            "type": config.device_type.value,
            "icon": config.icon,
            "versions": config.versions,
            "isActive": config.is_active,
            "portNaming": config.port_naming,
            "portStartIndex": config.port_start_index,
            "maxPorts": config.max_ports,
            "memory": config.memory,
            "cpu": config.cpu,
            "requiresImage": config.requires_image,
            "supportedImageKinds": config.supported_image_kinds,
            "documentationUrl": config.documentation_url,
            "licenseRequired": config.license_required,
            "tags": config.tags,
            "notes": config.notes,
            "consoleShell": config.console_shell,
            "readinessProbe": config.readiness_probe,
            "readinessPattern": config.readiness_pattern,
            "readinessTimeout": config.readiness_timeout,
            "vendorOptions": _get_vendor_options(config),
            "isBuiltIn": True,
        }
    else:
        # Check if it's a custom device
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")
        base_config = {**custom, "isBuiltIn": False}

    # Get overrides
    overrides = get_device_override(device_id) or {}

    # Compute effective configuration
    effective = {**base_config, **overrides}

    return {
        "base": base_config,
        "overrides": overrides,
        "effective": effective,
    }


@app.put("/vendors/{device_id}/config")
def update_device_config(
    device_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Update device configuration overrides.

    Allowed override fields:
    - memory: Memory requirement in MB
    - cpu: CPU cores required
    - maxPorts: Maximum interfaces
    - portNaming: Interface naming pattern
    - portStartIndex: Starting port number
    - readinessTimeout: Boot readiness timeout in seconds
    - vendorOptions: Vendor-specific options (e.g., zerotouchCancel)

    Returns the updated configuration.
    """
    from app.image_store import (
        find_custom_device,
        get_device_override,
        set_device_override,
    )
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device, _get_vendor_options

    # Allowed override fields
    ALLOWED_OVERRIDE_FIELDS = {
        "memory", "cpu", "maxPorts", "portNaming", "portStartIndex",
        "readinessTimeout", "vendorOptions"
    }

    # Filter payload to only allowed fields
    filtered_payload = {k: v for k, v in payload.items() if k in ALLOWED_OVERRIDE_FIELDS}

    if not filtered_payload:
        raise HTTPException(
            status_code=400,
            detail=f"No valid override fields provided. Allowed: {', '.join(ALLOWED_OVERRIDE_FIELDS)}"
        )

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Verify device exists
    is_built_in = canonical_id in VENDOR_CONFIGS
    if not is_built_in:
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")

    # Set override
    override = set_device_override(device_id, filtered_payload)

    # Return updated config
    return get_device_config(device_id, current_user)


@app.delete("/vendors/{device_id}/config")
def reset_device_config(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Reset device configuration to defaults by removing all overrides.

    Returns:
        Success message
    """
    from app.image_store import delete_device_override
    from agent.vendors import VENDOR_CONFIGS, get_kind_for_device

    # Resolve alias to canonical device ID
    canonical_id = get_kind_for_device(device_id)

    # Verify device exists
    is_built_in = canonical_id in VENDOR_CONFIGS
    if not is_built_in:
        from app.image_store import find_custom_device
        custom = find_custom_device(device_id)
        if not custom:
            raise HTTPException(status_code=404, detail="Device not found")

    # Delete override
    deleted = delete_device_override(device_id)
    if not deleted:
        return {"message": f"Device '{device_id}' has no overrides to reset"}

    return {"message": f"Device '{device_id}' reset to defaults"}


@app.get("/dashboard/metrics")
def get_dashboard_metrics(database: Session = Depends(db.get_db)) -> dict:
    """Get aggregated system metrics for the dashboard.

    Returns agent counts, container counts, CPU/memory usage, and lab stats.
    Labs running count is based on actual container presence, not database state.
    """
    import json

    # Get all hosts
    hosts = database.query(models.Host).all()
    online_agents = sum(1 for h in hosts if h.status == "online")
    total_agents = len(hosts)

    # Get all labs for mapping
    all_labs = database.query(models.Lab).all()
    labs_by_id = {lab.id: lab for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: lab.id for lab in all_labs}  # containerlab truncates

    def find_lab_id(prefix: str) -> str | None:
        """Find lab ID by prefix match."""
        if not prefix:
            return None
        if prefix in labs_by_id:
            return prefix
        if prefix in labs_by_prefix:
            return labs_by_prefix[prefix]
        for lab_id in labs_by_id:
            if lab_id.startswith(prefix):
                return lab_id
        return None

    # Aggregate resource usage from all online agents
    total_cpu = 0.0
    total_memory = 0.0
    total_memory_used = 0.0
    total_memory_total = 0.0
    total_disk_used = 0.0
    total_disk_total = 0.0
    total_containers_running = 0
    total_containers = 0
    online_count = 0
    labs_with_containers: set[str] = set()  # Track labs with running containers
    per_host: list[dict] = []  # Per-host breakdown for multi-host environments

    for host in hosts:
        if host.status != "online":
            continue
        online_count += 1
        try:
            usage = json.loads(host.resource_usage) if host.resource_usage else {}
            host_cpu = usage.get("cpu_percent", 0)
            host_memory = usage.get("memory_percent", 0)
            host_memory_used = usage.get("memory_used_gb", 0)
            host_memory_total = usage.get("memory_total_gb", 0)
            host_disk_percent = usage.get("disk_percent", 0)
            host_disk_used = usage.get("disk_used_gb", 0)
            host_disk_total = usage.get("disk_total_gb", 0)
            host_containers = usage.get("containers_running", 0)

            total_cpu += host_cpu
            total_memory += host_memory
            total_memory_used += host_memory_used
            total_memory_total += host_memory_total
            total_disk_used += host_disk_used
            total_disk_total += host_disk_total
            total_containers_running += host_containers
            total_containers += usage.get("containers_total", 0)

            # Track per-host data
            per_host.append({
                "id": host.id,
                "name": host.name,
                "cpu_percent": round(host_cpu, 1),
                "memory_percent": round(host_memory, 1),
                "memory_used_gb": host_memory_used,
                "memory_total_gb": host_memory_total,
                "storage_percent": round(host_disk_percent, 1),
                "storage_used_gb": host_disk_used,
                "storage_total_gb": host_disk_total,
                "containers_running": host_containers,
            })

            # Track which labs have running containers
            for container in usage.get("container_details", []):
                if container.get("status") == "running" and not container.get("is_system"):
                    lab_id = find_lab_id(container.get("lab_prefix", ""))
                    if lab_id:
                        labs_with_containers.add(lab_id)
        except (json.JSONDecodeError, TypeError):
            pass

    # Calculate averages
    avg_cpu = total_cpu / online_count if online_count > 0 else 0
    avg_memory = total_memory / online_count if online_count > 0 else 0

    # Storage: aggregate totals, calculate overall percent
    storage_percent = (total_disk_used / total_disk_total * 100) if total_disk_total > 0 else 0

    # Use container-based count as source of truth for running labs
    running_labs = len(labs_with_containers)

    # Determine if multi-host environment
    is_multi_host = total_agents > 1

    return {
        "agents": {"online": online_agents, "total": total_agents},
        "containers": {"running": total_containers_running, "total": total_containers},
        "cpu_percent": round(avg_cpu, 1),
        "memory_percent": round(avg_memory, 1),
        "memory": {
            "used_gb": round(total_memory_used, 2),
            "total_gb": round(total_memory_total, 2),
            "percent": round(avg_memory, 1),
        },
        "storage": {
            "used_gb": round(total_disk_used, 2),
            "total_gb": round(total_disk_total, 2),
            "percent": round(storage_percent, 1),
        },
        "labs_running": running_labs,
        "labs_total": len(all_labs),
        "per_host": per_host,
        "is_multi_host": is_multi_host,
    }


@app.get("/dashboard/metrics/containers")
def get_containers_breakdown(database: Session = Depends(db.get_db)) -> dict:
    """Get detailed container breakdown by lab."""
    import json

    hosts = database.query(models.Host).filter(models.Host.status == "online").all()
    all_labs = database.query(models.Lab).all()
    # Map both full ID and truncated prefix to lab info
    labs_by_id = {lab.id: lab.name for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: (lab.id, lab.name) for lab in all_labs}  # containerlab truncates to ~20 chars

    def find_lab(prefix: str) -> tuple[str | None, str | None]:
        """Find lab by prefix match."""
        if not prefix:
            return None, None
        # Try exact match first
        if prefix in labs_by_id:
            return prefix, labs_by_id[prefix]
        # Try prefix match (containerlab truncates lab IDs)
        if prefix in labs_by_prefix:
            return labs_by_prefix[prefix]
        # Try partial prefix match
        for lab_id, lab_name in labs_by_id.items():
            if lab_id.startswith(prefix):
                return lab_id, lab_name
        return None, None

    all_containers = []
    for host in hosts:
        try:
            usage = json.loads(host.resource_usage) if host.resource_usage else {}
            for container in usage.get("container_details", []):
                container["agent_name"] = host.name
                lab_id, lab_name = find_lab(container.get("lab_prefix", ""))
                container["lab_id"] = lab_id
                container["lab_name"] = lab_name
                all_containers.append(container)
        except (json.JSONDecodeError, TypeError):
            pass

    # Group by lab
    by_lab = {}
    system_containers = []
    for c in all_containers:
        if c.get("is_system"):
            system_containers.append(c)
        elif c.get("lab_id"):
            lab_id = c["lab_id"]
            if lab_id not in by_lab:
                by_lab[lab_id] = {"name": c["lab_name"], "containers": []}
            by_lab[lab_id]["containers"].append(c)
        else:
            # Orphan containerlab container (lab deleted but container still running)
            system_containers.append(c)

    return {
        "by_lab": by_lab,
        "system_containers": system_containers,
        "total_running": sum(1 for c in all_containers if c.get("status") == "running"),
        "total_stopped": sum(1 for c in all_containers if c.get("status") != "running"),
    }


@app.get("/dashboard/metrics/resources")
def get_resource_distribution(database: Session = Depends(db.get_db)) -> dict:
    """Get resource usage distribution by agent and lab."""
    import json

    hosts = database.query(models.Host).filter(models.Host.status == "online").all()
    all_labs = database.query(models.Lab).all()
    labs_by_id = {lab.id: lab.name for lab in all_labs}

    def find_lab_id(prefix: str) -> str | None:
        """Find lab ID by prefix match."""
        if not prefix:
            return None
        if prefix in labs_by_id:
            return prefix
        for lab_id in labs_by_id:
            if lab_id.startswith(prefix):
                return lab_id
        return None

    by_agent = []
    lab_containers = {}  # lab_id -> container count

    for host in hosts:
        usage = json.loads(host.resource_usage) if host.resource_usage else {}
        by_agent.append({
            "id": host.id,
            "name": host.name,
            "cpu_percent": usage.get("cpu_percent", 0),
            "memory_percent": usage.get("memory_percent", 0),
            "memory_used_gb": usage.get("memory_used_gb", 0),
            "memory_total_gb": usage.get("memory_total_gb", 0),
            "containers": usage.get("containers_running", 0),
        })

        # Count containers per lab (only non-system containers)
        for c in usage.get("container_details", []):
            if c.get("is_system"):
                continue
            lab_id = find_lab_id(c.get("lab_prefix", ""))
            if lab_id:
                lab_containers[lab_id] = lab_containers.get(lab_id, 0) + 1

    # Estimate lab resource usage by container proportion
    total_containers = sum(lab_containers.values()) or 1
    by_lab = [
        {
            "id": lab_id,
            "name": labs_by_id[lab_id],
            "container_count": count,
            "estimated_percent": round(count / total_containers * 100, 1),
        }
        for lab_id, count in lab_containers.items()
    ]

    return {"by_agent": by_agent, "by_lab": sorted(by_lab, key=lambda x: -x["container_count"])}
