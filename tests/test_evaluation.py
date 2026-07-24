from pathlib import Path

from scripts.evaluate_ollama import parse_validation, values_match


def test_validation_file_maps_expected_images() -> None:
    records = parse_validation(Path("tests/images/ImageTestValidation.md"))

    assert records["WellerFullProof.jpeg"]["proof"] == "114.0"
    assert records["WellerFullProof.jpeg"]["abv"] == "57.0"
    assert records["BlantonsStraightFromTheBarrel.jpeg"]["barrel_number"] == "2149"


def test_validation_comparison_normalizes_units_and_text() -> None:
    assert values_match("fill_level", "40%", 40)
    assert values_match("fill_level", "100%", 95)
    assert not values_match("fill_level", "100%", 85)
    assert values_match("proof", "93.0", 93)
    assert values_match("spirit_type", "Bourbon", "bourbon")
