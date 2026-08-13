"""Pure-Python reader for TIGER/Line RT (record-type) line files.

Why this exists: GDAL removed the OGR ``TIGER`` driver in 3.11 (RFC 108) and
only restored it in 3.13. On a GDAL in that window ``ogrinfo`` cannot open a
record-type directory at all, so every pre-2007 archive — seven of the nine
years this pipeline downloads — converts to nothing. This module reads the
files directly and emits GeoJSON, which ogr2ogr can ingest with any driver
build, so the conversion no longer depends on a driver that comes and goes.

Only the CompleteChain layer is reconstructed, because that is the only layer
the roads output needs:

  RT1  one fixed-width 228-byte record per complete chain: TLID, feature name,
       CFCC class, and the two endpoint coordinates.
  RT2  optional shape points that bend the chain between its endpoints, 10
       points per 208-byte record, ordered by RTSQ.

Coordinates are signed integers in millionths of a degree (+41776000 =
41.776000°N), NAD83 — which the Census treats as equivalent to WGS84 for these
products, the same assumption the GDAL driver makes.

Field offsets are identical across every year the pipeline fetches: the 1992
files carry .F5x extensions instead of .RTx but use the same record layout.
"""
from __future__ import annotations

import json
from pathlib import Path

# 0-based [start, end) slices into a 228-byte RT1 record.
_RT1 = {
    "TLID":   (5, 15),
    "FEDIRP": (17, 19),
    "FENAME": (19, 49),
    "FETYPE": (49, 53),
    "FEDIRS": (53, 55),
    "CFCC":   (55, 58),
    "ZIPL":   (106, 111),
    "ZIPR":   (111, 116),
}
_RT1_FRLONG, _RT1_FRLAT = (190, 200), (200, 209)
_RT1_TOLONG, _RT1_TOLAT = (209, 219), (219, 228)

# RT2: TLID, then RTSQ, then 10 (long, lat) pairs of 10+9 bytes from col 19.
_RT2_TLID, _RT2_RTSQ, _RT2_PT0 = (5, 15), (15, 18), 18
_RT2_NPTS, _RT2_LON, _RT2_LAT = 10, 10, 9

RT1_LEN = 228
RT2_LEN = 208

# 1992 uses .F5x for the same record types (.F51 == .RT1, .F52 == .RT2).
_EXT = {"1": (".RT1", ".F51"), "2": (".RT2", ".F52")}


def _coord(rec, span):
    """Signed millionths-of-a-degree field → float degrees, or None if blank."""
    s = rec[span[0]:span[1]].strip()
    if not s or s in ("+", "-"):
        return None
    try:
        v = int(s)
    except ValueError:
        return None
    return v / 1_000_000.0


def _txt(rec, key):
    return rec[_RT1[key][0]:_RT1[key][1]].strip()


def _records(path, length):
    """Yield fixed-width records, tolerating CRLF, LF, or no line breaks."""
    with open(path, "r", encoding="latin-1", errors="replace") as fh:
        blob = fh.read().replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in blob.split("\n") if ln]
    if lines and all(len(ln) >= length - 1 for ln in lines[:20]):
        for ln in lines:
            if len(ln) >= length - 1:
                yield ln.ljust(length)
        return
    # Some mirrors ship the records unbroken; fall back to fixed-size chunks.
    blob = blob.replace("\n", "")
    for i in range(0, len(blob) - length + 1, length):
        yield blob[i:i + length]


def _module_files(directory):
    """{basename: {'1': rt1_path, '2': rt2_path}} for every county in a dir."""
    mods = {}
    for p in Path(directory).rglob("*"):
        if not p.is_file():
            continue
        suf = p.suffix.upper()
        for rt, exts in _EXT.items():
            if suf in exts:
                mods.setdefault(p.stem.upper(), {})[rt] = p
    return {k: v for k, v in mods.items() if "1" in v}


def _shape_points(rt2_path):
    """{TLID: [(lon, lat), ...]} in RTSQ order."""
    pts = {}
    if rt2_path is None:
        return pts
    for rec in _records(rt2_path, RT2_LEN):
        tlid = rec[_RT2_TLID[0]:_RT2_TLID[1]].strip()
        if not tlid:
            continue
        try:
            seq = int(rec[_RT2_RTSQ[0]:_RT2_RTSQ[1]])
        except ValueError:
            continue
        run = []
        off = _RT2_PT0
        for _ in range(_RT2_NPTS):
            lon = _coord(rec, (off, off + _RT2_LON))
            lat = _coord(rec, (off + _RT2_LON, off + _RT2_LON + _RT2_LAT))
            off += _RT2_LON + _RT2_LAT
            if lon is None or lat is None or (lon == 0.0 and lat == 0.0):
                continue           # unused slot in a partly-filled record
            run.append((lon, lat))
        pts.setdefault(tlid, []).append((seq, run))
    return {t: [p for _, run in sorted(runs) for p in run] for t, runs in pts.items()}


def chains(directory, cfcc_prefix=None):
    """Yield GeoJSON LineString features for the CompleteChain layer.

    cfcc_prefix (e.g. 'A') keeps only that CFCC class — 'A' is roads.
    """
    for base, files in sorted(_module_files(directory).items()):
        shapes = _shape_points(files.get("2"))
        for rec in _records(files["1"], RT1_LEN):
            if rec[0] != "1":
                continue
            cfcc = _txt(rec, "CFCC")
            if cfcc_prefix and not cfcc.upper().startswith(cfcc_prefix.upper()):
                continue
            fr = (_coord(rec, _RT1_FRLONG), _coord(rec, _RT1_FRLAT))
            to = (_coord(rec, _RT1_TOLONG), _coord(rec, _RT1_TOLAT))
            if None in fr or None in to:
                continue
            tlid = _txt(rec, "TLID")
            coords = [fr, *shapes.get(tlid, []), to]
            name = " ".join(x for x in (_txt(rec, "FEDIRP"), _txt(rec, "FENAME"),
                                        _txt(rec, "FETYPE"), _txt(rec, "FEDIRS")) if x)
            yield {
                "type": "Feature",
                "properties": {
                    "MODULE": base, "TLID": tlid, "CFCC": cfcc,
                    "FEDIRP": _txt(rec, "FEDIRP"), "FENAME": _txt(rec, "FENAME"),
                    "FETYPE": _txt(rec, "FETYPE"), "FEDIRS": _txt(rec, "FEDIRS"),
                    "FULLNAME": name,
                    "ZIPL": _txt(rec, "ZIPL"), "ZIPR": _txt(rec, "ZIPR"),
                },
                "geometry": {"type": "LineString",
                             "coordinates": [[x, y] for x, y in coords]},
            }


def write_geojson(directory, out_path, cfcc_prefix=None):
    """Stream the chains to a GeoJSON file. Returns the feature count."""
    n = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection",'
                 '"crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}},'
                 '"features":[')
        for feat in chains(directory, cfcc_prefix):
            fh.write(("," if n else "") + json.dumps(feat))
            n += 1
        fh.write("]}")
    return n
