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


def test_blantons_straight_from_the_barrel_does_not_match_the_original_expression() -> None:
    product = verified_product("Blanton's Straight From The Barrel")

    assert product
    assert product["name"] == "Blanton's Straight From The Barrel"
    assert product["release"] == "Straight From The Barrel"
    assert product.get("proof") is None


def test_image_validation_products_have_curated_reusable_profiles() -> None:
    expected = {
        "Weller Antique 107": (107.0, 53.5),
        "Weller Special Reserve": (90.0, 45.0),
        "Eagle Rare 10 Year": (90.0, 45.0),
        "Colonel E.H. Taylor Jr. Small Batch": (100.0, 50.0),
        "Buffalo Trace": (90.0, 45.0),
    }

    for name, (proof, abv) in expected.items():
        product = verified_product(name)
        assert product
        assert product["proof"] == proof
        assert product["abv"] == abv
        assert product["size"] == "750ml"
        assert "msrp" not in product


def test_verified_product_can_be_matched_from_label_ocr() -> None:
    product = verified_product_from_text(
        "W. L. WELLER\nFULL PROOF\nKENTUCKY STRAIGHT BOURBON WHISKEY"
    )

    assert product
    assert product["name"] == "W.L. Weller Full Proof"


def test_new_riff_8_year_verified_values() -> None:
    product = verified_product("New Riff Kentucky Straight Bourbon Whiskey 8 Years")

    assert product
    assert product["name"] == "New Riff 8 Year Old Kentucky Straight Bourbon Whiskey"
    assert product["distilled_by"] == "New Riff Distilling"
    assert product["mash_bill"] == "65% Corn, 30% Rye, 5% Malted Barley"
    assert product["size"] == "750ml"
