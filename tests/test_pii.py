from app.pii import scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd_credit_card_and_passport() -> None:
    out = scrub_text("CCCD 079203001234, card 4111 1111 1111 1111, passport B1234567")
    assert "079203001234" not in out
    assert "4111" not in out
    assert "B1234567" not in out
    assert "REDACTED_CCCD" in out
    assert "REDACTED_CREDIT_CARD" in out
    assert "REDACTED_PASSPORT" in out


def test_card_is_not_mistaken_for_phone() -> None:
    out = scrub_text("card 0123-4567-8901-2345")
    assert "REDACTED_CREDIT_CARD" in out
    assert "REDACTED_PHONE_VN" not in out


def test_scrub_address_keyword() -> None:
    out = scrub_text("Địa chỉ: 12 Nguyen Trai, Ha Noi")
    assert "Nguyen Trai" not in out
    assert "REDACTED_ADDRESS" in out


def test_plain_text_is_unchanged() -> None:
    assert scrub_text("How do I debug tail latency?") == "How do I debug tail latency?"
