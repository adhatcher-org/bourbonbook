from bourbonbook.catalog import verified_product, verified_product_from_text


def test_weller_full_proof_verified_values() -> None:
    product = verified_product("Weller Full Proof")

    assert product
    assert product["name"] == "W.L. Weller Full Proof"
    assert product["proof"] == 114.0
    assert product["abv"] == 57.0
    assert product["distilled_by"] == "Buffalo Trace Distillery"
    assert "secondary_price" not in product


def test_unknown_product_has_no_verified_override() -> None:
    assert verified_product("An entirely unknown bottle") is None


def test_blantons_verbose_name_maps_to_verified_values() -> None:
    product = verified_product(
        "Blanton's The Original Single Barrel Kentucky Straight Bourbon Whiskey"
    )

    assert product
    assert product["name"] == "Blanton's Original Single Barrel"
    assert product["proof"] == 93.0
    assert product["abv"] == 46.5


def test_verified_product_can_be_matched_from_label_ocr() -> None:
    product = verified_product_from_text(
        "W. L. WELLER\nFULL PROOF\nKENTUCKY STRAIGHT BOURBON WHISKEY"
    )

    assert product
    assert product["name"] == "W.L. Weller Full Proof"
