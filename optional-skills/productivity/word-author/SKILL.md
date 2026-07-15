---
name: word-author
description: Produce .docx files with tables, headings, and images.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [word, docx, python-docx, belge, rapor, tablo, sözleşme]
    category: productivity
    related_skills: [excel-author, pptx-author, ocr-and-documents]
---

# word-author

Headless Python ile .docx belgesi üret. Raporlar, teklifler, sözleşme
taslakları, protokoller — tamamı `python-docx` ile birkaç satır kodla.

`excel-author` ile birlikte kullan: Excel'den alınan sayıları Word'e
çekerek kaynak-izlenebilir belgeler oluştur.

## When to Use

- Yapılandırılmış metin raporu teslim etmek gerektiğinde (.docx)
- Birden fazla bölüm, tablo ve liste içeren belgeler üretmek için
- Şablondan (`.docx` temel dosya) içerik oluşturulacağında
- `excel-author` çıktısını bir Word raporuna aktarmak gerektiğinde
- OCR ile çıkarılan veriden biçimlendirilmiş belge üretilecekken

## Prerequisites

```bash
pip install python-docx
```

Tablodan resme dönüştürme ve görsel gömme için (opsiyonel):

```bash
pip install Pillow
```

## How to Run

```bash
# Temel rapor üret
python scripts/word_helper.py --template bos --out ./out/rapor.docx \
    --baslik "Q2 Durum Raporu" --yazar "Hermes Agent"

# Şablondan üret
python scripts/word_helper.py --template ./templates/sirket.docx \
    --out ./out/teklif.docx --baslik "Hizmet Teklifi"

# Veri JSON'dan tablo olarak ekle
python scripts/word_helper.py --out ./out/rapor.docx \
    --tablo veriler.json --tablo-baslik "Parsel Listesi"
```

## Quick Reference

| Görev | python-docx API |
|-------|-----------------|
| Belge oluştur | `Document()` veya `Document("sablon.docx")` |
| Başlık | `doc.add_heading("Başlık", level=1)` |
| Paragraf | `doc.add_paragraph("metin")` |
| Kalın/italik | `run.bold = True` / `run.italic = True` |
| Tablo | `doc.add_table(rows=N, cols=M)` |
| Madde listesi | `doc.add_paragraph("md", style="List Bullet")` |
| Resim | `doc.add_picture("resim.png", width=Inches(4))` |
| Sayfa sonu | `doc.add_page_break()` |
| Kaydet | `doc.save("cikti.docx")` |

## Procedure

### Adım 1: Basit belge iskelet kur

```python
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

doc = Document()          # boş belge
# Şablondan: doc = Document("./templates/sirket.docx")

# Kenar boşlukları
sec = doc.sections[0]
sec.top_margin = Inches(1)
sec.bottom_margin = Inches(1)
sec.left_margin = Inches(1.2)
sec.right_margin = Inches(1.2)

Path("./out").mkdir(exist_ok=True)
doc.save("./out/rapor.docx")
```

### Adım 2: Başlık ve kapak sayfası ekle

```python
from docx.enum.text import WD_ALIGN_PARAGRAPH

h = doc.add_heading("Proje Durum Raporu", level=0)
h.alignment = WD_ALIGN_PARAGRAPH.CENTER

alt = doc.add_paragraph()
alt.alignment = WD_ALIGN_PARAGRAPH.CENTER
alt.add_run("Hazırlayan: Hermes Agent").italic = True

doc.add_paragraph()
r = alt.add_run("Tarih: 15 Temmuz 2026")
r.font.size = Pt(10)
r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

doc.add_page_break()
```

### Adım 3: İçerik bölümleri

```python
# Level 1 başlık
doc.add_heading("Yönetici Özeti", level=1)
doc.add_paragraph(
    "Bu rapor 2026 Q2 dönemine ait operasyonel metrikleri özetlemektedir."
)

# Level 2 alt başlık
doc.add_heading("Temel Bulgular", level=2)

# Madde listesi
doc.add_paragraph("Gelir %18 artış gösterdi.", style="List Bullet")
doc.add_paragraph("Müşteri memnuniyeti 4.7/5 olarak ölçüldü.", style="List Bullet")
doc.add_paragraph("Yeni kullanıcı sayısı hedefin %12 üzerinde.", style="List Bullet")

# Numaralı liste
doc.add_paragraph("Pazarlama kampanyası başlat.", style="List Number")
doc.add_paragraph("Q3 hedeflerini gözden geçir.", style="List Number")
```

### Adım 4: Tablo ekle

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def hucre_rengi_ata(hucre, hex_renk: str) -> None:
    """Tablo hücresine arka plan rengi ver."""
    tc_pr = hucre._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_renk)
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:val"), "clear")
    tc_pr.append(shd)

satirlar_verisi = [
    ["Ürün", "Q1", "Q2", "Değişim"],
    ["Widget A", "1200", "1416", "+18%"],
    ["Widget B", "850", "935", "+10%"],
    ["Widget C", "320", "384", "+20%"],
]

tablo = doc.add_table(rows=len(satirlar_verisi), cols=4)
tablo.style = "Table Grid"

for r_idx, satir in enumerate(satirlar_verisi):
    for c_idx, deger in enumerate(satir):
        hucre = tablo.rows[r_idx].cells[c_idx]
        hucre.text = deger
        if r_idx == 0:
            hucre.paragraphs[0].runs[0].bold = True
            hucre_rengi_ata(hucre, "2E74B5")
            hucre.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        elif r_idx % 2 == 1:
            hucre_rengi_ata(hucre, "DEEAF1")
```

### Adım 5: Resim gömme

```python
from docx.shared import Inches

# Yerel dosya
doc.add_picture("./grafik.png", width=Inches(5.5))

# Resim altına açıklama ekle
aciklama = doc.add_paragraph("Şekil 1 — Q2 Gelir Dağılımı")
aciklama.alignment = WD_ALIGN_PARAGRAPH.CENTER
aciklama.style = "Caption"  # (şablonda Caption stili varsa)
```

### Adım 6: Üstbilgi ve altbilgi

```python
from docx.oxml.ns import qn

section = doc.sections[0]
header = section.header
footer = section.footer

# Üstbilgi
header.paragraphs[0].text = "Gizli — Dahili Kullanım"
header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

# Altbilgi: sayfa numarası
from docx.oxml import OxmlElement
def sayfa_numarasi_ekle(paragraph):
    run = paragraph.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    run._r.append(fld)
    ins = OxmlElement("w:instrText")
    ins.text = " PAGE "
    run._r.append(ins)
    fld2 = OxmlElement("w:fldChar")
    fld2.set(qn("w:fldCharType"), "end")
    run._r.append(fld2)

footer_para = footer.paragraphs[0]
footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
sayfa_numarasi_ekle(footer_para)
```

### Adım 7: Kaydet ve doğrula

```python
Path("./out").mkdir(exist_ok=True)
doc.save("./out/rapor.docx")

# Hızlı doğrulama
from docx import Document as _D
kontrol = _D("./out/rapor.docx")
print(f"Belge OK: {len(kontrol.paragraphs)} paragraf, "
      f"{len(kontrol.tables)} tablo")
```

## Pitfalls

- **Stiller şablona bağlıdır.** `"List Bullet"` veya `"Table Grid"`
  gibi stiller boş belgede çalışır, ancak özel şablonda farklı isim
  taşıyabilir. `doc.styles` listesiyle mevcut stilleri kontrol et.
- **Tablo hücresi birden fazla paragraf içerebilir.** `hucre.text = x`
  her atamada YENİ paragraf ekler, eski içeriği silmez.
  Temiz atama için: `hucre.paragraphs[0].text = x` kullan ya da
  `hucre.paragraphs[0].runs[0].text = x`.
- **Resim dönüştürme.** python-docx doğrudan PDF veya SVG gömemez;
  önce PNG'ye çevir (`Pillow` veya `cairosvg`).
- **Merge edilmiş hücreler.** `tablo.cell(0,0).merge(tablo.cell(0,2))`
  satır birleştirme için; sonraki erişimde birleştirilmiş hücreyi
  yalnızca sol-üst köşeden oku.
- **LibreOffice dönüşümü.** `.docx → .pdf` dönüşümü için LibreOffice
  gerekir: `soffice --headless --convert-to pdf rapor.docx`.
  `recalc.py`'deki aynı örüntüyü kullan.

## Verification

```bash
python3 -c "
from docx import Document
doc = Document('./out/rapor.docx')
assert len(doc.paragraphs) >= 3, 'Paragraf eksik'
assert len(doc.tables) >= 1, 'Tablo eksik'
print(f'OK — {len(doc.paragraphs)} paragraf, {len(doc.tables)} tablo')
"
```
