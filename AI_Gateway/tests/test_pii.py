"""Feature/Expectation: Log-only PII redaction."""

from gateway.observability.pii import PIIRedactor


def test_logger_redacts_email_and_card():
    """Feature: Log PII redaction
    Expectation: PIIRedactor replaces emails and PAN-like digits in log payloads.
    """
    redactor = PIIRedactor(active=True)
    text = redactor.redact_string("Contact buyer@example.com card 4111111111111111")
    assert "buyer@example.com" not in text
    assert "[REDACTED EMAIL]" in text
    assert "[REDACTED CREDIT_CARD]" in text
    nested = redactor.redact_dict({"note": "ssn 123-45-6789"})
    assert "123-45-6789" not in nested["note"]
    assert "[REDACTED SSN]" in nested["note"]
