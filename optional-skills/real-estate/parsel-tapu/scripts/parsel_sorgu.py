#!/usr/bin/env python3
"""TKGM Parsel Sorgulama — CBS API ve web scraping fallback.

Kullanım:
    python parsel_sorgu.py --il 34 --ilce 100 --ada 123 --parsel 4
    python parsel_sorgu.py --lat 41.015137 --lon 28.979530
    python parsel_sorgu.py --il 34 --ilce 100 --ada 123 --parsel 4 --json
    python parsel_sorgu.py --batch parseller.csv --out sonuclar.xlsx
    python parsel_sorgu.py --batch parseller.csv --out sonuclar.json

Çıktı alanları:
    ada, parsel, il, ilce, mahalle, alan_m2, nitelik, koordinatlar,
    harita_url, hata (başarısız sorgularda)
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# TKGM CBS API uç noktaları
# ---------------------------------------------------------------------------

# Kadastral parsel sorgu (koordinat → parsel)
_CBS_PARSEL_BY_COORD = (
    "https://cbsservis.tkgm.gov.tr/megsiswebapi.3/api/dataservis/parsel"
    "?islem=PARSELGeoJSON&koord={lon},{lat}&projeksiyon=3857"
)

# Ada-parsel tabanlı sorgu
_CBS_PARSEL_BY_ADAPARSEL = (
    "https://cbsservis.tkgm.gov.tr/megsiswebapi.3/api/dataservis/parsel"
    "?islem=PARSELGeoJSON&il={il}&ilce={ilce}&ada={ada}&parsel={parsel}"
)

# Açık parsel sorgu servisi (alternatif, daha basit yanıt)
_TKGM_PARSEL_API = (
    "https://parselsorgu.tkgm.gov.tr/api/uyap/parsel"
    "?il={il}&ilce={ilce}&ada={ada}&parsel={parsel}"
)

# Google Maps harita linki (konum doğrulama için)
_MAPS_URL = "https://www.google.com/maps?q={lat},{lon}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://parselsorgu.tkgm.gov.tr/",
}

_RETRY_DELAY = 1.0  # saniye, hız sınırı aşmamak için
_DEFAULT_TIMEOUT = 20  # saniye


def _get(url: str, timeout: int = _DEFAULT_TIMEOUT, retry: int = 3) -> Optional[dict]:
    """HTTP GET; başarısızlıkta retry kadar tekrar dene. Döner dict veya None."""
    try:
        import requests
    except ImportError:
        print("HATA: 'requests' yüklü değil. pip install requests", file=sys.stderr)
        sys.exit(1)

    for attempt in range(1, retry + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            print(f"  [uyarı] Zaman aşımı (deneme {attempt}/{retry}): {url[:80]}…",
                  file=sys.stderr)
        except requests.exceptions.HTTPError as exc:
            print(f"  [uyarı] HTTP {exc.response.status_code} (deneme {attempt}/{retry})",
                  file=sys.stderr)
        except Exception as exc:
            print(f"  [uyarı] Bağlantı hatası (deneme {attempt}/{retry}): {exc}",
                  file=sys.stderr)
        if attempt < retry:
            time.sleep(_RETRY_DELAY * attempt)
    return None


def _normalize_cbs_feature(feature: dict) -> dict:
    """GeoJSON feature'dan düz dict çıkar."""
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}

    # Koordinat merkezini hesapla (Polygon için)
    lat, lon = None, None
    coords = geom.get("coordinates")
    if coords and geom.get("type") == "Polygon":
        ring = coords[0]
        if ring:
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            lon = sum(lons) / len(lons)
            lat = sum(lats) / len(lats)
    elif coords and geom.get("type") == "Point":
        lon, lat = coords[0], coords[1]

    result: dict = {
        "ada": str(props.get("ADA") or props.get("ada") or ""),
        "parsel": str(props.get("PARSEL") or props.get("parsel") or ""),
        "il": str(props.get("IL_ADI") or props.get("il_adi") or props.get("il") or ""),
        "il_kodu": str(props.get("IL_KODU") or props.get("il_kodu") or ""),
        "ilce": str(props.get("ILCE_ADI") or props.get("ilce_adi") or props.get("ilce") or ""),
        "ilce_kodu": str(props.get("ILCE_KODU") or props.get("ilce_kodu") or ""),
        "mahalle": str(props.get("MAHALLE_ADI") or props.get("mahalle_adi") or ""),
        "alan_m2": props.get("ALAN") or props.get("alan") or props.get("YUZOLCUMU"),
        "nitelik": str(props.get("NITELIK") or props.get("nitelik") or ""),
        "pafta": str(props.get("PAFTA") or props.get("pafta") or ""),
        "koordinatlar": json.dumps(geom) if geom else None,
    }

    if lat is not None and lon is not None:
        result["merkez_lat"] = round(lat, 6)
        result["merkez_lon"] = round(lon, 6)
        result["harita_url"] = _MAPS_URL.format(lat=round(lat, 6), lon=round(lon, 6))

    try:
        if result["alan_m2"] is not None:
            result["alan_m2"] = float(result["alan_m2"])
    except (TypeError, ValueError):
        pass

    return result


def sorgu_ada_parsel(il: str, ilce: str, ada: str, parsel: str,
                     retry: int = 3) -> dict:
    """Ada-parsel numarası ile TKGM CBS API'den parsel bilgisi çek."""
    # Birincil: CBS API
    url = _CBS_PARSEL_BY_ADAPARSEL.format(il=il, ilce=ilce, ada=ada, parsel=parsel)
    data = _get(url, retry=retry)
    if data:
        features = data.get("features") or (data if isinstance(data, list) else [])
        if features:
            return _normalize_cbs_feature(features[0])

    # Fallback: TKGM UYAP API
    url2 = _TKGM_PARSEL_API.format(il=il, ilce=ilce, ada=ada, parsel=parsel)
    data2 = _get(url2, retry=retry)
    if data2:
        # Bu API farklı format döner; normalize et
        if isinstance(data2, list) and data2:
            item = data2[0]
        elif isinstance(data2, dict):
            item = data2
        else:
            item = {}
        return {
            "ada": str(item.get("ada", ada)),
            "parsel": str(item.get("parsel", parsel)),
            "il": str(item.get("il", il)),
            "ilce": str(item.get("ilce", ilce)),
            "mahalle": str(item.get("mahalle", "")),
            "alan_m2": item.get("alan"),
            "nitelik": str(item.get("nitelik", "")),
            "pafta": str(item.get("pafta", "")),
            "koordinatlar": None,
            "hata": None,
        }

    return {
        "ada": ada,
        "parsel": parsel,
        "il": il,
        "ilce": ilce,
        "hata": "CBS API ve UYAP API yanıt vermedi — web scraping gerekebilir",
    }


def sorgu_koordinat(lat: float, lon: float, retry: int = 3) -> dict:
    """Enlem/boylam koordinatı ile parsel bul (WGS84)."""
    # CBS API koordinat sorgusunda Mercator (EPSG:3857) kullanıyor
    # WGS84 → Mercator dönüşümü
    try:
        import math
        x = lon * 20037508.34 / 180
        y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
        y = y * 20037508.34 / 180
    except Exception:
        x, y = lon, lat  # dönüşüm başarısız; ham koordinat gönder

    url = _CBS_PARSEL_BY_COORD.format(lat=y, lon=x)
    data = _get(url, retry=retry)
    if data:
        features = data.get("features") or (data if isinstance(data, list) else [])
        if features:
            return _normalize_cbs_feature(features[0])

    return {
        "hata": f"Koordinat ({lat}, {lon}) için parsel bulunamadı",
        "merkez_lat": lat,
        "merkez_lon": lon,
        "harita_url": _MAPS_URL.format(lat=lat, lon=lon),
    }


def toplu_sorgu_csv(csv_dosya: str, retry: int = 3) -> list[dict]:
    """CSV dosyasından toplu parsel sorgula. Gerekli sütunlar: il,ilce,ada,parsel."""
    sonuclar = []
    with open(csv_dosya, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        satirlar = list(reader)

    print(f"{len(satirlar)} parsel sorgulanacak…", file=sys.stderr)
    for i, satir in enumerate(satirlar, 1):
        il = satir.get("il", "").strip()
        ilce = satir.get("ilce", "").strip()
        ada = satir.get("ada", "").strip()
        parsel = satir.get("parsel", "").strip()
        aciklama = satir.get("aciklama", "").strip()

        print(f"  [{i}/{len(satirlar)}] İl:{il} İlçe:{ilce} Ada:{ada} Parsel:{parsel}",
              file=sys.stderr)

        if ada and parsel:
            sonuc = sorgu_ada_parsel(il, ilce, ada, parsel, retry=retry)
        elif satir.get("lat") and satir.get("lon"):
            sonuc = sorgu_koordinat(float(satir["lat"]), float(satir["lon"]), retry=retry)
        else:
            sonuc = {"hata": "CSV satırında ada+parsel veya lat+lon eksik", **satir}

        if aciklama:
            sonuc["aciklama"] = aciklama
        sonuclar.append(sonuc)
        time.sleep(_RETRY_DELAY)  # hız sınırı koruması

    return sonuclar


def kaydet_excel(sonuclar: list[dict], dosya: str) -> None:
    """Sorgu sonuçlarını Excel'e yaz (openpyxl)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        print("HATA: 'openpyxl' yüklü değil. pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Parsel Sorgu"

    sutunlar = ["ada", "parsel", "il", "ilce", "mahalle", "alan_m2",
                "nitelik", "pafta", "merkez_lat", "merkez_lon",
                "harita_url", "aciklama", "hata"]
    baslik_font = Font(bold=True, color="FFFFFF")
    baslik_fill = PatternFill("solid", fgColor="1F4E79")

    for c, baslik in enumerate(sutunlar, 1):
        hucre = ws.cell(row=1, column=c, value=baslik.upper())
        hucre.font = baslik_font
        hucre.fill = baslik_fill

    for r, sonuc in enumerate(sonuclar, 2):
        for c, sutun in enumerate(sutunlar, 1):
            ws.cell(row=r, column=c, value=sonuc.get(sutun))

    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    Path(dosya).parent.mkdir(parents=True, exist_ok=True)
    wb.save(dosya)
    print(f"Excel kaydedildi: {dosya}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="TKGM Parsel Sorgulama")
    ap.add_argument("--il", help="İl kodu (örn. 34)")
    ap.add_argument("--ilce", help="İlçe kodu (TKGM/UAVT)")
    ap.add_argument("--ada", help="Ada numarası")
    ap.add_argument("--parsel", help="Parsel numarası")
    ap.add_argument("--lat", type=float, help="Enlem (WGS84)")
    ap.add_argument("--lon", type=float, help="Boylam (WGS84)")
    ap.add_argument("--batch", help="Toplu sorgu için CSV dosyası")
    ap.add_argument("--out", help="Çıktı dosyası (.json veya .xlsx)")
    ap.add_argument("--retry", type=int, default=3, help="Yeniden deneme sayısı")
    ap.add_argument("--json", action="store_true", help="Çıktıyı JSON olarak stdout'a yaz")
    args = ap.parse_args()

    # Toplu mod
    if args.batch:
        sonuclar = toplu_sorgu_csv(args.batch, retry=args.retry)
        out = args.out or "sonuclar.json"
        if out.endswith(".xlsx"):
            kaydet_excel(sonuclar, out)
        else:
            Path(out).write_text(json.dumps(sonuclar, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            print(f"JSON kaydedildi: {out}", file=sys.stderr)
        return

    # Tek sorgu modu
    if args.lat is not None and args.lon is not None:
        sonuc = sorgu_koordinat(args.lat, args.lon, retry=args.retry)
    elif args.il and args.ilce and args.ada and args.parsel:
        sonuc = sorgu_ada_parsel(args.il, args.ilce, args.ada, args.parsel,
                                 retry=args.retry)
    else:
        ap.error("--il, --ilce, --ada, --parsel veya --lat, --lon gerekli")

    if args.out:
        out = args.out
        if out.endswith(".xlsx"):
            kaydet_excel([sonuc], out)
        else:
            Path(out).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    else:
        print(json.dumps(sonuc, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
