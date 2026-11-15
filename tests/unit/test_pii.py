from codeforge.privacy.pii import (
    generate_verhoeff_check_digit,
    is_luhn_valid,
    is_verhoeff_valid,
    mask_obj,
    mask_text,
)


def test_mask_api_keys():
    assert mask_text("Use key sk-1234567890abcdef1234 now") == "Use key [SECRET] now"
    assert (
        mask_text("My groq is gsk_1234567890abcdefghij12345678")
        == "My groq is [SECRET]"
    )
    assert mask_text("AWS key AKIAIOSFODNN7EXAMPLE") == "AWS key [SECRET]"


def test_mask_email():
    assert mask_text("Send to test.user+tag@domain.co.in please") == "Send to [EMAIL] please"


def test_mask_credit_card():
    # Valid Visa number passes Luhn
    card = "4111 1111 1111 1111"
    assert is_luhn_valid(card.replace(" ", ""))
    assert mask_text(f"Pay with {card}") == "Pay with [CARD]"

    # Invalid Luhn number should not be masked
    invalid_card = "order 1234567890123"
    assert not is_luhn_valid("1234567890123")
    assert mask_text(invalid_card) == invalid_card


def test_mask_aadhaar():
    prefix = "23456789012"
    check = generate_verhoeff_check_digit(prefix)
    valid_aadhaar = f"{prefix}{check}"
    assert is_verhoeff_valid(valid_aadhaar)
    formatted = f"{valid_aadhaar[:4]} {valid_aadhaar[4:8]} {valid_aadhaar[8:]}"
    assert mask_text(f"Aadhaar is {formatted}") == "Aadhaar is [AADHAAR]"

    # 12-digit number failing Verhoeff is still masked (typos / test data must not leak)
    invalid_aadhaar = "23456789012" + str((check + 1) % 10)
    assert not is_verhoeff_valid(invalid_aadhaar)
    assert mask_text(f"id {invalid_aadhaar}") == "id [AADHAAR]"
    assert mask_text("aadhaar 2345-6789-0123") == "aadhaar [AADHAAR]"
    assert mask_text(f"id{valid_aadhaar}") == "id[AADHAAR]"  # glued to text


def test_mask_indian_ids():
    assert mask_text("PAN ABCDE1234F ok") == "PAN [PAN] ok"
    assert mask_text("ifsc HDFC0001234") == "ifsc [IFSC]"
    assert mask_text("pay rahul@upi now") == "pay [UPI] now"
    assert mask_text("acct no 123456789012 here") == "acct no [BANK_ACCOUNT] here"
    assert mask_text("mail a.b@example.com") == "mail [EMAIL]"


def test_indian_id_negatives():
    for s in ["sum a list of 1234567890 numbers", "def f(): return x1234567", "ABCDE12345"]:
        assert mask_text(s) == s


def test_mask_phone():
    assert mask_text("Call +91 9876543210 immediately") == "Call [PHONE] immediately"
    assert mask_text("My phone is 9876543210") == "My phone is [PHONE]"
    assert mask_text("Intl phone +1-555-1234567 here") == "Intl phone [PHONE] here"


def test_unmasked_negatives():
    # Regular mathematical expressions, dates, order numbers
    assert mask_text("calculate factorial(20)") == "calculate factorial(20)"
    assert mask_text("year 2024 is great") == "year 2024 is great"
    assert mask_text("order 1234567890123") == "order 1234567890123"


def test_mask_obj_nested():
    data = {
        "user": "Alice",
        "email": "alice@example.com",
        "nested": [
            {"secret": "sk-abcdef1234567890XYZ"},
            "9876543210",
        ],
    }
    masked = mask_obj(data)
    assert masked["email"] == "[EMAIL]"
    assert masked["nested"][0]["secret"] == "[SECRET]"
    assert masked["nested"][1] == "[PHONE]"
    assert masked["user"] == "Alice"


def test_aadhaar_masked_with_irregular_grouping():
    from codeforge.privacy.pii import mask_text

    assert mask_text("validate my adhar card 23456 7890 123") == "validate my adhar card [AADHAAR]"
    assert mask_text("x = 12345") == "x = 12345"
