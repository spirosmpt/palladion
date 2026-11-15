import io
import zipfile

import pytest
import shapefile

from palladion.datasets import copernicus

PRJ_WGS84 = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]]]'


def _layer(zf, name, kind, rows, fields):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=kind)
    for f in fields:
        w.field(f, "C", 40)
    for geom, rec in rows:
        if kind == shapefile.POLYGON:
            w.poly(geom)
        else:
            w.point(*geom)
        w.record(*rec)
    w.close()
    for ext, buf in ((".shp", shp), (".shx", shx), (".dbf", dbf)):
        zf.writestr(name + ext, buf.getvalue())
    zf.writestr(name + ".prj", PRJ_WGS84)


def package(grade_values):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        ring = [[(23.95, 38.04), (23.99, 38.04), (23.99, 38.06), (23.95, 38.06), (23.95, 38.04)]]
        _layer(zf, "EMSR300_02RAFINA_01DELINEATION_MAP_v2_observed_event_a", shapefile.POLYGON,
               [(ring, ["Burned area"])], ["event_type"])
        pts = [((23.96 + i * 0.001, 38.05), [g]) for i, g in enumerate(grade_values)]
        _layer(zf, "EMSR300_02RAFINA_02GRADING_MAP_v2_built_up_p", shapefile.POINT, pts, ["damage_gra"])
        # Land use and roads carry damage grades too, but are not buildings.
        _layer(zf, "EMSR300_02RAFINA_02GRADING_MAP_v2_natural_land_use_a", shapefile.POLYGON,
               [(ring, ["Destroyed"])], ["damage_gra"])
    return buf.getvalue()


def test_reads_perimeter_and_damage_grades():
    grades = ["Destroyed", "Damaged", "Possibly damaged", "No visible damage", ""]
    c = copernicus.read_package(package(grades))
    layers = {li.name.split("_v2_")[1] for li in c.layers}
    assert layers == {"observed_event_a", "built_up_p", "natural_land_use_a"}
    per = copernicus.perimeter_geojson(c, "test")["features"][0]["geometry"]
    assert per["type"] == "Polygon"
    dmg = copernicus.damage_geojson(c, "test")["features"]
    assert sorted(f["properties"]["grade"] for f in dmg) == ["damaged", "destroyed", "possibly_damaged"]


@pytest.mark.parametrize("raw,grade", [
    ("Completely Destroyed", "destroyed"), ("Highly Damaged", "damaged"),
    ("Moderately Damaged", "possibly_damaged"),
    ("Negligible to slight damage", "no_visible_damage"), ("Not Applicable", None), ("", None),
])
def test_grade_wording_from_different_years(raw, grade):
    assert copernicus.normalise_grade(raw) == grade


def test_projected_data_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        ring = [[(735000, 4215000), (738000, 4215000), (738000, 4218000), (735000, 4215000)]]
        _layer(zf, "x_observed_event_a", shapefile.POLYGON, [(ring, ["a"])], ["t"])
    with pytest.raises(ValueError, match="reprojection"):
        copernicus.read_package(buf.getvalue())
