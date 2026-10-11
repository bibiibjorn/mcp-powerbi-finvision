"""Tests for bulk translation setter."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from core.operations.culture_crud_manager import CultureCrudManager


def _amo_patch(model, err=None):
    """Patch CultureCrudManager._amo_session to yield (server, model, err)."""

    @contextmanager
    def _fake(self):
        yield (MagicMock(), model, err)

    return patch.object(CultureCrudManager, "_amo_session", _fake)


def _build_model(culture_name="fr-FR", with_culture=True):
    """Build a fake TOM model that supports the operations bulk_set_translations needs."""
    table = MagicMock()
    table.Name = "Fact"
    measure_sales = MagicMock()
    measure_sales.Name = "Sales"
    measure_bad = MagicMock()
    measure_bad.Name = "BadMeasure"
    measure_good = MagicMock()
    measure_good.Name = "Good"
    column_year = MagicMock()
    column_year.Name = "Year"

    table_fact = MagicMock()
    table_fact.Name = "Fact"
    table_fact.Measures.Find = lambda n: {
        "Sales": measure_sales,
        "Good": measure_good,
        "BadMeasure": None,
    }.get(n)
    table_fact.Columns.Find = lambda n: {"Year": column_year}.get(n)
    table_fact.Hierarchies.Find = lambda n: None

    table_date = MagicMock()
    table_date.Name = "Date"
    table_date.Columns.Find = lambda n: {"Year": column_year}.get(n)
    table_date.Measures.Find = lambda n: None
    table_date.Hierarchies.Find = lambda n: None

    model = MagicMock()
    model.Tables.Find = lambda n: {"Fact": table_fact, "Date": table_date}.get(n)

    if with_culture:
        culture = MagicMock()
        culture.Name = culture_name
        culture.ObjectTranslations.SetTranslation = MagicMock()
        model.Cultures.Find = lambda n: culture if n == culture_name else None
    else:
        model.Cultures.Find = lambda n: None
        culture = None
    return model, culture


def _patch_tom():
    """Inject a fake Microsoft.AnalysisServices.Tabular module into sys.modules.

    The import statement `import Microsoft.AnalysisServices.Tabular as TOM`
    requires every package in the dotted chain to be present in sys.modules,
    so we patch all three.
    """
    import sys

    fake = MagicMock()
    fake.TranslatedProperty.Caption = "CAP"
    fake.TranslatedProperty.Description = "DESC"
    fake.TranslatedProperty.DisplayFolder = "DF"
    parent_ms = MagicMock()
    parent_as = MagicMock()
    parent_as.Tabular = fake
    parent_ms.AnalysisServices = parent_as
    return patch.dict(
        sys.modules,
        {
            "Microsoft": parent_ms,
            "Microsoft.AnalysisServices": parent_as,
            "Microsoft.AnalysisServices.Tabular": fake,
        },
    )


def test_bulk_set_translations_applies_each_and_saves_once():
    model, culture = _build_model()
    items = [
        {
            "object_type": "measure",
            "name": "Sales",
            "property": "caption",
            "value": "Ventes",
            "table_name": "Fact",
        },
        {"object_type": "table", "name": "Fact", "property": "caption", "value": "Faits"},
        {
            "object_type": "column",
            "name": "Year",
            "property": "description",
            "value": "Annee",
            "table_name": "Date",
        },
    ]
    with _amo_patch(model), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(
            culture_name="fr-FR", translations=items
        )
    assert result["success"] is True
    assert result["applied"] == 3
    assert result["failed"] == 0
    assert culture.ObjectTranslations.SetTranslation.call_count == 3
    model.SaveChanges.assert_called_once()


def test_bulk_set_translations_partial_failure_collected():
    model, _culture = _build_model()
    items = [
        {
            "object_type": "measure",
            "name": "Good",
            "property": "caption",
            "value": "OK",
            "table_name": "Fact",
        },
        {
            "object_type": "measure",
            "name": "BadMeasure",
            "property": "caption",
            "value": "X",
            "table_name": "Fact",
        },
    ]
    with _amo_patch(model), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(
            culture_name="fr-FR", translations=items
        )
    assert result["applied"] == 1
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    assert "BadMeasure" in result["errors"][0]["item"]["name"]
    model.SaveChanges.assert_called_once()


def test_bulk_set_translations_no_culture():
    """Model is None (e.g. AMO unavailable / not connected)."""
    with _amo_patch(model=None, err="Not connected"), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(culture_name="fr-FR", translations=[])
    assert result["success"] is False
    assert "not connected" in result["error"].lower()


def test_bulk_set_translations_empty_list():
    model, _ = _build_model()
    with _amo_patch(model), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(culture_name="fr-FR", translations=[])
    assert result["success"] is True
    assert result["applied"] == 0
    assert result["failed"] == 0
    model.SaveChanges.assert_not_called()


def test_bulk_set_translations_missing_required_field():
    model, _ = _build_model()
    items = [
        {"object_type": "measure", "name": "Sales", "property": "caption", "table_name": "Fact"}
    ]
    with _amo_patch(model), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(
            culture_name="fr-FR", translations=items
        )
    assert result["applied"] == 0
    assert result["failed"] == 1
    assert "missing" in result["errors"][0]["error"].lower()


def test_bulk_set_translations_culture_not_found():
    model, _ = _build_model(with_culture=False)
    with _amo_patch(model), _patch_tom():
        result = CultureCrudManager().bulk_set_translations(culture_name="es-ES", translations=[])
    assert result["success"] is False
    assert "es-ES" in result["error"]
