"""Outbound notifications."""

from .resend_mailer import (
    EmailError,
    analysis_recipients,
    email_configured,
    send_analysis_email,
    send_analysis_email_async,
)

__all__ = [
    "EmailError",
    "analysis_recipients",
    "email_configured",
    "send_analysis_email",
    "send_analysis_email_async",
]
