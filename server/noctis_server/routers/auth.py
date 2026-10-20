"""Регистрация, вход, обновление токена, восстановление пароля."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..password_policy import policy_error
from ..models import AuditLog, AuthSession, PasswordReset, User, UserSettings, utcnow
from ..ratelimit import auth_limit
from ..schemas import (ChangePasswordIn, ForgotIn, LoginIn, RefreshIn, RegisterIn, ResetIn,
                       TokenPair)
from ..security import hash_password, new_token, sha256, verify_password
from ..services import as_aware, get_role, issue_tokens

log = logging.getLogger("noctis.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(auth_limit)])

# Заглушка для выравнивания времени ответа при неизвестном e-mail (защита от перебора логинов)
_DUMMY_HASH = hash_password("dummy-password")


@router.post("/register", response_model=TokenPair, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not body.consent:
        raise HTTPException(422, "Consent to personal data processing is required")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name,
                role_id=get_role(db, "user").id, consent_at=utcnow())
    user.settings = UserSettings(language=body.language)
    db.add(user)
    db.commit()
    return issue_tokens(db, user, request.headers.get("user-agent", ""))


@router.post("/login", response_model=TokenPair)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    user.last_login_at = utcnow()
    if user.role.name == "admin":
        db.add(AuditLog(admin_id=user.id, admin_email=user.email, action="login",
                        ip=request.client.host if request.client else ""))
    db.commit()
    return issue_tokens(db, user, request.headers.get("user-agent", ""))


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """Ротация: старый refresh-токен погашается. Повторное использование погашенного
    токена трактуется как кража — все сессии пользователя отзываются."""
    sess = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == sha256(body.refresh_token)))
    if sess is None:
        raise HTTPException(401, "Invalid refresh token")
    if sess.revoked:
        for s in db.scalars(select(AuthSession).where(AuthSession.user_id == sess.user_id)):
            s.revoked = True
        db.commit()
        raise HTTPException(401, "Refresh token reuse detected; please sign in again")
    if as_aware(sess.expires_at) < utcnow():
        raise HTTPException(401, "Refresh token expired")
    sess.revoked = True
    user = db.get(User, sess.user_id)
    db.commit()
    return issue_tokens(db, user, request.headers.get("user-agent", ""))


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    sess = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == sha256(body.refresh_token)))
    if sess:
        sess.revoked = True
        db.commit()


@router.post("/forgot-password", status_code=202)
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Всегда 202 — не раскрываем, зарегистрирован ли e-mail. Письмо отправляется почтовым
    адаптером (SMTP настраивается при развёртывании); в разработке токен пишется в лог."""
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    result: dict = {"detail": "If the email exists, a reset link has been sent"}   # одинаковый ответ для любого адреса
    if user:
        for old in db.scalars(select(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used.is_(False))):
            old.used = True                                      # действует только последний выданный токен
        token = new_token()
        db.add(PasswordReset(user_id=user.id, token_hash=sha256(token),
                             expires_at=utcnow() + timedelta(minutes=settings.reset_token_minutes)))
        db.commit()
        log.info("Password reset requested for user %s", user.id)    # токен в журнал и в ответ не попадает
        if settings.expose_reset_token and not settings.is_production:
            log.warning("DEV password reset token for user %s: %s", user.id, token)
            result["dev_token"] = token
    return result


@router.post("/reset-password", status_code=204)
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    pr = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == sha256(body.token)))
    if pr is None or pr.used or as_aware(pr.expires_at) < utcnow():
        raise HTTPException(400, "Invalid or expired reset token")
    user = db.get(User, pr.user_id)
    reason = policy_error(body.new_password, user.email, user.display_name)
    if reason:
        raise HTTPException(422, reason)
    user.password_hash = hash_password(body.new_password)
    pr.used = True
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        s.revoked = True  # после смены пароля все сессии завершаются
    db.commit()


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    reason = policy_error(body.new_password, user.email, user.display_name)
    if reason:
        raise HTTPException(422, reason)
    user.password_hash = hash_password(body.new_password)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        s.revoked = True       # после смены пароля все сессии завершаются: нужно войти заново
    db.commit()
