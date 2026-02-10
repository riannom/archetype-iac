from __future__ import annotations

import json
import secrets

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app import db, models, schemas
from app.auth import authenticate_user, create_access_token, get_current_user, hash_password
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
oauth = OAuth()

if settings.oidc_issuer_url and settings.oidc_client_id:
    oauth.register(
        name="oidc",
        server_metadata_url=f"{settings.oidc_issuer_url}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": settings.oidc_scopes},
    )


@router.post("/login", response_model=schemas.TokenOut)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    database: Session = Depends(db.get_db),
) -> schemas.TokenOut:
    from app.services.audit import AuditService

    if not settings.local_auth_enabled:
        raise HTTPException(status_code=403, detail="Local auth is disabled")

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else None))

    user = authenticate_user(database, form_data.username, form_data.password)
    if not user:
        AuditService.log(
            database, "login_failed", ip_address=ip,
            details={"identifier": form_data.username},
        )
        database.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    AuditService.log(
        database, "login_success", user_id=user.id, ip_address=ip,
    )
    token = create_access_token(user.id)
    database.commit()
    return schemas.TokenOut(access_token=token)


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)) -> schemas.UserOut:
    return schemas.UserOut.model_validate(current_user)


@router.get("/oidc/login")
async def oidc_login(request: Request):
    if not settings.oidc_issuer_url or not settings.oidc_client_id or not settings.oidc_redirect_uri:
        raise HTTPException(status_code=503, detail="OIDC not configured")
    return await oauth.oidc.authorize_redirect(request, settings.oidc_redirect_uri)


@router.get("/oidc/callback")
async def oidc_callback(request: Request, database: Session = Depends(db.get_db)):
    if not settings.oidc_issuer_url or not settings.oidc_client_id:
        raise HTTPException(status_code=503, detail="OIDC not configured")
    try:
        token = await oauth.oidc.authorize_access_token(request)
        user_info = await oauth.oidc.parse_id_token(request, token)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="OIDC response missing email")

    user = database.query(models.User).filter(models.User.email == email).first()
    if not user:
        # Derive username from preferred_username claim or email prefix
        preferred = user_info.get("preferred_username", "")
        import re
        username = preferred.lower().strip() if preferred else email.split("@")[0].lower()
        username = re.sub(r"[^a-zA-Z0-9._-]", "_", username)
        if not username or not username[0].isalpha():
            username = "user_" + username
        username = username[:32]
        while len(username) < 3:
            username = username + "_"
        # Handle collisions
        base_username = username
        counter = 2
        while database.query(models.User).filter(models.User.username == username).first():
            suffix = f"_{counter}"
            username = base_username[: 32 - len(suffix)] + suffix
            counter += 1

        random_password = secrets.token_urlsafe(24)
        user = models.User(
            username=username,
            email=email,
            hashed_password=hash_password(random_password),
            global_role=settings.oidc_default_role,
        )
        database.add(user)
        database.commit()
        database.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account deactivated")

    access_token = create_access_token(user.id)
    if settings.oidc_app_redirect_url:
        return RedirectResponse(f"{settings.oidc_app_redirect_url}?token={access_token}")
    return schemas.TokenOut(access_token=access_token)


@router.get("/preferences", response_model=schemas.UserPreferencesOut)
def get_preferences(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserPreferencesOut:
    """Get current user's notification and canvas preferences."""
    prefs = database.query(models.UserPreferences).filter(
        models.UserPreferences.user_id == current_user.id
    ).first()

    if not prefs:
        # Return defaults
        return schemas.UserPreferencesOut()

    # Parse JSON fields
    try:
        notification_settings = json.loads(prefs.notification_settings) if prefs.notification_settings else {}
        canvas_settings = json.loads(prefs.canvas_settings) if prefs.canvas_settings else {}
    except json.JSONDecodeError:
        notification_settings = {}
        canvas_settings = {}

    return schemas.UserPreferencesOut(
        notification_settings=schemas.NotificationSettings(**notification_settings) if notification_settings else schemas.NotificationSettings(),
        canvas_settings=schemas.CanvasSettings(**canvas_settings) if canvas_settings else schemas.CanvasSettings(),
    )


@router.patch("/preferences", response_model=schemas.UserPreferencesOut)
def update_preferences(
    update: schemas.UserPreferencesUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.UserPreferencesOut:
    """Update current user's notification and canvas preferences."""
    prefs = database.query(models.UserPreferences).filter(
        models.UserPreferences.user_id == current_user.id
    ).first()

    if not prefs:
        prefs = models.UserPreferences(user_id=current_user.id)
        database.add(prefs)

    # Merge existing settings with updates
    if update.notification_settings:
        existing = {}
        try:
            existing = json.loads(prefs.notification_settings) if prefs.notification_settings else {}
        except json.JSONDecodeError:
            pass
        # Deep merge the settings
        new_settings = update.notification_settings.model_dump()
        for key, value in new_settings.items():
            if isinstance(value, dict) and key in existing and isinstance(existing[key], dict):
                existing[key].update(value)
            else:
                existing[key] = value
        prefs.notification_settings = json.dumps(existing)

    if update.canvas_settings:
        existing = {}
        try:
            existing = json.loads(prefs.canvas_settings) if prefs.canvas_settings else {}
        except json.JSONDecodeError:
            pass
        # Deep merge the settings
        new_settings = update.canvas_settings.model_dump()
        for key, value in new_settings.items():
            if isinstance(value, dict) and key in existing and isinstance(existing[key], dict):
                existing[key].update(value)
            else:
                existing[key] = value
        prefs.canvas_settings = json.dumps(existing)

    database.commit()
    database.refresh(prefs)

    # Parse and return
    try:
        notification_settings = json.loads(prefs.notification_settings) if prefs.notification_settings else {}
        canvas_settings = json.loads(prefs.canvas_settings) if prefs.canvas_settings else {}
    except json.JSONDecodeError:
        notification_settings = {}
        canvas_settings = {}

    return schemas.UserPreferencesOut(
        notification_settings=schemas.NotificationSettings(**notification_settings) if notification_settings else schemas.NotificationSettings(),
        canvas_settings=schemas.CanvasSettings(**canvas_settings) if canvas_settings else schemas.CanvasSettings(),
    )
