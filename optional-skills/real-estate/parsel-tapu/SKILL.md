---
name: parsel-tapu
description: Query Turkish land parcels and produce deed reports.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [tapu, parsel, tkgm, kadastro, gayrimenkul, gis, rapor, turkey]
    category: real-estate
    related_skills: [excel-author, word-author, ocr-and-documents]
---

# Parsel & Tapu Sorgulama

Türkiye'deki taşınmazlar için TKGM (Tapu ve Kadastro Genel Müdürlüğü)
veri kaynaklarından parsel bilgisi çek, tapu durumunu sorgula ve
Excel/Word formatında rapor üret.

## When to Use

- Belirli bir ada-parsel numarasına ait mülk bilgisi istendiğinde
- Taşınmazın koordinat, alan, imar durumu bilgisi gerektiğinde
- Birden fazla parsel için toplu sorgulama ve karşılaştırma yapılacağında
- Sonuçları Excel tablosu veya Word raporu olarak sunmak gerektiğinde
- `ocr-and-documents` ile taranan tapu belgelerinden veri çıkarılacağında

## Prerequisites

```bash
pip install requests openpyxl python-docx
```

Opsiyonel (koordinat haritalaması ve GIS işlemleri için):

```bash
pip install shapely pyproj
```

Ortam değişkeni gerekmez. TKGM parsel sorgulama kamuya açıktır.
e-Tapu veya yetkilendirme gerektiren uçlar için:

```bash
# ~/.hermes/.env
ETAPU_USERNAME=tc_kimlik_no
ETAPU_PASSWORD=sifre
```

## How to Run

```bash
# Tek parsel sorgula (ada-parsel numarası ile)
python scripts/parsel_sorgu.py --il 34 --ilce 100 --ada 123 --parsel 4

# Koordinat ile sorgula (WGS84 enlem/boylam)
python scripts/parsel_sorgu.py --lat 41.015137 --lon 28.979530

# JSON çıktısı
python scripts/parsel_sorgu.py --il 34 --ilce 100 --ada 123 --parsel 4 --json

# Toplu sorgu (CSV dosyasından)
python scripts/parsel_sorgu.py --batch parseller.csv --out sonuclar.xlsx

# Rapor üret (Excel)
python scripts/tapu_raporu.py --input sonuclar.json --format xlsx

# Rapor üret (Word)
python scripts/tapu_raporu.py --input sonuclar.json --format docx \
    --baslik "Taşınmaz Analiz Raporu"
```

## Quick Reference

| Kaynak | Amaç | URL |
|--------|------|-----|
| TKGM Parsel Sorgu | Parsel geometri + bilgi | `parselsorgu.tkgm.gov.tr` |
| TKGM CBS Servisi | API tabanlı koordinat/parsel sorgu | `cbsservis.tkgm.gov.tr` |
| e-Tapu | Yetkili tapu kütük sorgulama | `www.etapu.gov.tr` |
| Belediye İmar | İmar durumu / KAKS / TAKS | İl bazlı (belediye API) |

| Alan | Format | Örnek |
|------|--------|-------|
| İl kodu | 2 basamaklı sayı | 34 (İstanbul) |
| İlçe kodu | TKGM ilçe kodu | 100 |
| Ada | 1–4 basamaklı sayı | 123 |
| Parsel | 1–4 basamaklı sayı | 4 |

## Procedure

### Adım 1: Veri kaynağını belirle

Üç katman halinde dene; bir üstteki çalışırsa alt katmana geçme.

```
1. TKGM CBS API  →  JSON yanıtı, yapılandırılmış, API anahtarı gerektirmez
2. parselsorgu.tkgm.gov.tr  →  `browser_navigate` + `web_extract` ile scraping
3. e-Tapu portalı  →  TC kimlik girişi gerektirir (kısıtlı bilgi)
```

### Adım 2: TKGM CBS API ile sorgula (Birincil Yöntem)

TKGM'nin kamuya açık CBS web servisi ada-parsel üzerinden sorgulanabilir:

```bash
python scripts/parsel_sorgu.py --il 34 --ilce 100 --ada 123 --parsel 4
```

Script şu alanları döner:

- `parsel_id`, `ada`, `parsel`, `il`, `ilce`, `mahalle`
- `alan_m2` — kadastral alan (m²)
- `koordinatlar` — WKT veya GeoJSON geometri
- `nitelik` — arsa, tarla, bahçe vb.
- `kayit_no`, `pafta`

### Adım 3: Koordinat tabanlı sorgu

Enlem/boylam bilinen bir noktanın hangi parselde olduğunu bul:

```bash
python scripts/parsel_sorgu.py --lat 41.015137 --lon 28.979530
```

### Adım 4: Web scraping fallback

CBS API yanıt vermezse `web_extract` + `browser_navigate` ile:

```
browser_navigate("https://parselsorgu.tkgm.gov.tr")
# → Ada/Parsel alanlarını doldur
# → web_extract ile sonuç tablosunu oku
# → JSON'a normalize et
```

### Adım 5: Toplu sorgulama

`parseller.csv` formatı:

```csv
il,ilce,ada,parsel,aciklama
34,100,123,4,Kadıköy projem
34,101,55,12,Üsküdar iş yeri
06,200,88,3,Ankara merkez
```

```bash
python scripts/parsel_sorgu.py --batch parseller.csv --out sonuclar.xlsx
```

### Adım 6: Rapor üret

Tüm bulunan parseller için özet rapor:

```bash
# Excel: her parsel bir satır, sütunlar ada/parsel/alan/nitelik vb.
python scripts/tapu_raporu.py --input sonuclar.json --format xlsx

# Word: yönetici özeti + parsel tablosu + konum linkleri
python scripts/tapu_raporu.py --input sonuclar.json --format docx \
    --baslik "Kadıköy Taşınmaz Analiz Raporu" \
    --tarih 2026-07-15
```

### Adım 7: İmar bilgisi (Ek)

İmar durumu belediye bazlı değişir:

```python
# İstanbul Büyükşehir Belediyesi için örnek
web_extract(urls=["https://sehiratlasi.ibb.gov.tr/"])

# Alternatif: belediye e-İmar portalları
web_search(query="Kadıköy belediyesi imar durumu sorgulama API")
```

### Adım 8: Tapu kütük bilgisi (e-Tapu)

Malik adı, ipotekler ve şerhler yalnızca yetkili kullanıcılara açıktır.
Eğer `ETAPU_USERNAME` ve `ETAPU_PASSWORD` ayarlandıysa:

```python
# browser_navigate ile e-Tapu girişi yap
# Giriş sonrası tapu kütük görüntüleme sayfasına git
# Parsel bilgilerini gir, sonucu çek
# ÖNEMLİ: Yalnızca kendi taşınmazın veya
# hukuki yetkiye sahip olduğun taşınmazlar için kullan
```

## Pitfalls

- **İlçe kodu UAVT/TKGM kodudur, posta kodu değildir.** Yanlış ilçe
  kodu geçersiz sorgu döndürür. Doğru kodu bulmak için:
  `web_search("TKGM ilçe kodları listesi {il_adı}")`.
- **CBS API zaman zaman kararsız olabilir.** Zaman aşımında 30 saniye
  bekle ve tekrar dene; scriptte `--retry 3` bayrağı mevcuttur.
- **Koordinat sistemi.** TKGM ITRF96/TM veya WGS84 kullanır. Dışa
  aktarırken projeksiyon dönüşümü gerekebilir (`pyproj` kullan).
- **Toplu sorguda hız sınırı.** CBS API art arda sorguları kısıtlar;
  script sorgular arası 1 saniyelik gecikme ekler, bunu kaldırma.
- **e-Tapu gizlilik.** Malik ve şerh bilgisi gizlidir, yalnızca
  yetkili kullanıcı erişebilir. Bu skill yetkisiz erişim denemez.
- **Mahalle kodu.** Bazı API uçları il+ilçe+mahalle kodunu birlikte
  ister. Mahalle kodu için UAVT'ı kullan.

## Verification

```bash
# Sorgu çıktısını doğrula
python scripts/parsel_sorgu.py --il 34 --ilce 100 --ada 1 --parsel 1 --json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
              assert d.get('alan_m2') is not None, 'alan_m2 eksik'; \
              print('OK:', d.get('ada'), d.get('parsel'), d.get('alan_m2'), 'm²')"

# Excel raporu doğrula
python3 -c "
import openpyxl
wb = openpyxl.load_workbook('./out/tapu_raporu.xlsx')
ws = wb.active
assert ws.max_row > 1, 'Veri satırı yok'
print(f'Rapor OK: {ws.max_row - 1} parsel satırı')
"

# Word raporu doğrula
python3 -c "
from docx import Document
doc = Document('./out/tapu_raporu.docx')
assert len(doc.paragraphs) > 5, 'Rapor içeriği yetersiz'
print(f'Rapor OK: {len(doc.paragraphs)} paragraf')
"
```
