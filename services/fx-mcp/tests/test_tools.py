import json

import pytest

from conftest import RATES, call

pytestmark = pytest.mark.anyio


async def test_lists_tools(mcp_client):
    tools = {t.name for t in (await mcp_client.list_tools()).tools}
    assert tools == {"convert_currency", "list_rates"}


async def test_inr_to_usd(mcp_client):
    # 1598 INR / 84.10 INR-per-USD = 19.0012 USD
    assert await call(mcp_client, "convert_currency", {"amount": 1598, "from_currency": "INR", "to_currency": "USD"}) == (False, {
        "amount": "1598", "from": "INR", "to": "USD", "rate": "0.011891", "converted": "19.00", "rates_as_of": "2026-10-01",
    })


async def test_cross_rate_usd_to_eur_goes_through_base(mcp_client):
    # 100 USD = 8410 INR = 8410 / 91.50 EUR = 91.91 EUR
    _, result = await call(mcp_client, "convert_currency", {"amount": 100, "from_currency": "usd", "to_currency": "eur"})
    assert (result["from"], result["to"], result["converted"]) == ("USD", "EUR", "91.91")


async def test_decimal_input_has_no_float_noise(mcp_client):
    _, result = await call(mcp_client, "convert_currency", {"amount": 0.1, "from_currency": "USD", "to_currency": "INR"})
    assert (result["amount"], result["converted"]) == ("0.1", "8.41")


async def test_zero_and_same_currency(mcp_client):
    _, result = await call(mcp_client, "convert_currency", {"amount": 0, "from_currency": "INR", "to_currency": "USD"})
    assert result["converted"] == "0.00"
    _, result = await call(mcp_client, "convert_currency", {"amount": 5, "from_currency": "EUR", "to_currency": "EUR"})
    assert (result["rate"], result["converted"]) == ("1.000000", "5.00")


async def test_unknown_currency_lists_supported(mcp_client):
    is_error, text = await call(mcp_client, "convert_currency", {"amount": 1, "from_currency": "INR", "to_currency": "GBP"})
    assert is_error and "unsupported currency 'GBP'" in text and "EUR, INR, JPY, USD" in text


async def test_invalid_input_rejected_by_schema(mcp_client):
    for args in ({"amount": -1, "from_currency": "INR", "to_currency": "USD"},
                 {"amount": 1, "from_currency": "RUPEES", "to_currency": "USD"}):
        is_error, _ = await call(mcp_client, "convert_currency", args)
        assert is_error, args


async def test_list_rates_rebased(mcp_client):
    _, result = await call(mcp_client, "list_rates")
    assert result["base"] == "INR" and result["rates"]["USD"] == "84.100000" and result["rates"]["INR"] == "1.000000"
    _, result = await call(mcp_client, "list_rates", {"base": "USD"})
    assert result["rates"]["USD"] == "1.000000"
    assert result["rates"]["INR"] == "0.011891"   # 1 INR in USD
    assert result["rates"]["EUR"] == "1.087990"   # 91.50 / 84.10


async def test_rates_are_reread_on_every_call(mcp_client, rates_file):
    # Simulates Kubernetes updating the mounted ConfigMap: no restart needed.
    rates_file.write_text(json.dumps({**RATES, "as_of": "2026-10-15", "rates": {**RATES["rates"], "USD": "80"}}))
    _, result = await call(mcp_client, "convert_currency", {"amount": 800, "from_currency": "INR", "to_currency": "USD"})
    assert (result["converted"], result["rates_as_of"]) == ("10.00", "2026-10-15")


async def test_broken_rates_file_is_a_clean_error(mcp_client, rates_file):
    rates_file.write_text("{not json")
    is_error, text = await call(mcp_client, "list_rates")
    assert is_error and "exchange rates unavailable" in text
