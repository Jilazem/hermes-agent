#!/usr/bin/env python3
"""Word (DOCX) belge yardımcısı — python-docx üstüne kolaylık katmanı.

Kullanım:
    python word_helper.py --out ./out/rapor.docx --baslik "Rapor Başlığı"
    python word_helper.py --out ./out/rapor.docx --baslik "Rapor" --yazar "Ad Soyad"
    python word_helper.py --template ./templates/sablon.docx --out ./out/teklif.docx
    python word_helper.py --out ./out/tablo.docx --tablo veriler.json \
                          --tablo-baslik "Özet Tablo"

Doğrudan çalıştırıldığında örnek bir rapor belgesi üretir.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# python-docx — module-level import (tek seferlik)
# ---------------------------------------------------------------------------
try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    _DOCX_AVAILABLE = True
except ImportError:
    _DOCX_AVAILABLE = False


def _require_docx() -> None:
    """python-docx yüklü değilse açıklayıcı hata verip çık."""
    if not _DOCX_AVAILABLE:
        print("HATA: 'python-docx' yüklü değil. Kur: pip install python-docx",
              file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def hucre_rengi(hucre, hex_renk: str) -> None:
    """Tablo hücresine düz arka plan rengi ata (OOXML düzeyinde)."""
    _require_docx()
    tc_pr = hucre._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_renk)
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:val"), "clear")
    tc_pr.append(shd)


def sayfa_numarasi_ekle(paragraph) -> None:
    """Paragrafın sonuna Word sayfa-numarası alanı ekle."""
    _require_docx()
    run = paragraph.add_run()
    for tag, metin in [("begin", None), (None, " PAGE "), ("end", None)]:
        if tag:
            fld = OxmlElement("w:fldChar")
            fld.set(qn("w:fldCharType"), tag)
            run._r.append(fld)
        else:
            ins = OxmlElement("w:instrText")
            ins.text = metin
            run._r.append(ins)


# ---------------------------------------------------------------------------
# Yüksek seviye yardımcılar
# ---------------------------------------------------------------------------

def belge_olustur(sablon: Optional[str] = None):
    """Boş veya şablondan yeni belge döner."""
    _require_docx()
    if sablon and Path(sablon).exists():
        return Document(sablon)
    return Document()


def kenar_bosluklari_ayarla(doc, ust=1.0, alt=1.0, sol=1.2, sag=1.2) -> None:
    """Sayfa kenar boşluklarını inç cinsinden ayarla."""
    sec = doc.sections[0]
    sec.top_margin = Inches(ust)
    sec.bottom_margin = Inches(alt)
    sec.left_margin = Inches(sol)
    sec.right_margin = Inches(sag)


def kapak_sayfasi_ekle(doc, baslik: str, yazar: str = "",
                        tarih: str = "") -> None:
    """Ortalanmış başlık, yazar ve tarih ile kapak sayfası ekle."""
    h = doc.add_heading(baslik, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if h.runs:
        h.runs[0].font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    doc.add_paragraph()

    if yazar:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = para.add_run(f"Hazırlayan: {yazar}")
        r.italic = True
        r.font.size = Pt(12)

    if tarih:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = para.add_run(f"Tarih: {tarih}")
        r.font.size = Pt(11)
        r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    doc.add_page_break()


def baslikli_bolum_ekle(doc, baslik_metni: str, icerik: str = "",
                         seviye: int = 1) -> None:
    """Başlık + isteğe bağlı paragraf ekle."""
    doc.add_heading(baslik_metni, level=seviye)
    if icerik:
        doc.add_paragraph(icerik)


def madde_listesi_ekle(doc, maddeler: list[str], numarali: bool = False) -> None:
    """Madde işaretli veya numaralı liste ekle."""
    stil = "List Number" if numarali else "List Bullet"
    for madde in maddeler:
        doc.add_paragraph(madde, style=stil)


def tablo_ekle(doc, satirlar: list[list[Any]],
               baslik_satiri: bool = True,
               baslik_rengi: str = "2E74B5",
               satir_zebra: bool = True) -> None:
    """2 boyutlu liste'den biçimlendirilmiş Word tablosu ekle.

    Args:
        satirlar: [[sütun1, sütun2, ...], [satır2_1, ...], ...]
        baslik_satiri: İlk satır başlık mı?
        baslik_rengi: Başlık arka plan rengi (hex, '#' olmadan)
        satir_zebra: Çift/tek satırlar değişimli renkle mi gösterilsin?
    """
    if not satirlar:
        return

    n_sutun = max(len(satir) for satir in satirlar)
    tablo = doc.add_table(rows=len(satirlar), cols=n_sutun)
    tablo.style = "Table Grid"

    for r_idx, satir in enumerate(satirlar):
        for c_idx, deger in enumerate(satir):
            hucre = tablo.rows[r_idx].cells[c_idx]
            metin = str(deger) if deger is not None else ""
            if hucre.paragraphs:
                p = hucre.paragraphs[0]
            else:
                p = hucre.add_paragraph()
            p.clear()
            run = p.add_run(metin)

            if baslik_satiri and r_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                hucre_rengi(hucre, baslik_rengi)
            elif satir_zebra and r_idx % 2 == (0 if baslik_satiri else 1):
                hucre_rengi(hucre, "DEEAF1")

    doc.add_paragraph()


def json_tablo_ekle(doc, json_veri: Any, tablo_baslik: str = "") -> None:
    """JSON listesi veya dict'ten otomatik tablo oluştur."""
    if isinstance(json_veri, dict):
        json_veri = [json_veri]
    if not json_veri or not isinstance(json_veri, list):
        return

    if tablo_baslik:
        doc.add_heading(tablo_baslik, level=2)

    # Sütunları çıkar
    sutunlar: list[str] = []
    for satir in json_veri:
        for k in (satir.keys() if isinstance(satir, dict) else []):
            if k not in sutunlar:
                sutunlar.append(k)

    if not sutunlar:
        return

    tablo_verisi = [sutunlar]
    for satir in json_veri:
        if isinstance(satir, dict):
            tablo_verisi.append([satir.get(k, "") for k in sutunlar])

    tablo_ekle(doc, tablo_verisi)


def ustbilgi_altbilgi_ekle(doc, ustbilgi_metni: str = "",
                             altbilgi_sayfa_no: bool = True) -> None:
    """Üstbilgi metni ve/veya sayfalı altbilgi ekle."""
    section = doc.sections[0]

    if ustbilgi_metni:
        section.different_first_page_header_footer = False
        para = section.header.paragraphs[0]
        para.text = ustbilgi_metni
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    if altbilgi_sayfa_no:
        footer_para = section.footer.paragraphs[0]
        footer_para.clear()
        footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sayfa_numarasi_ekle(footer_para)


def resim_ekle(doc, resim_yolu: str, genislik_inc: float = 5.0,
               aciklama: str = "") -> None:
    """Belgeye resim ve isteğe bağlı açıklama ekle."""
    if not Path(resim_yolu).exists():
        doc.add_paragraph(f"[Resim bulunamadi: {resim_yolu}]")
        return
    doc.add_picture(resim_yolu, width=Inches(genislik_inc))
    if aciklama:
        para = doc.add_paragraph(aciklama)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER


def belge_kaydet(doc, dosya: str) -> None:
    """Belgeyi kaydet; çıktı dizinini yoksa oluştur."""
    Path(dosya).parent.mkdir(parents=True, exist_ok=True)
    doc.save(dosya)
    print(f"Belge kaydedildi: {dosya}")


def belge_dogrula(dosya: str) -> dict:
    """Kaydedilen belgeyi yükle ve temel istatistik döndür."""
    _require_docx()
    doc = Document(dosya)
    sonuc = {
        "dosya": dosya,
        "paragraf_sayisi": len(doc.paragraphs),
        "tablo_sayisi": len(doc.tables),
        "toplam_kelime": sum(len(p.text.split()) for p in doc.paragraphs),
    }
    print(f"Dogrulama OK - {sonuc}")
    return sonuc


# ---------------------------------------------------------------------------
# CLI arayüzü
# ---------------------------------------------------------------------------

def ornek_rapor_uret(dosya: str, baslik: str, yazar: str, tarih: str,
                      sablon: Optional[str] = None) -> None:
    """Demo: temel bölümler, tablo ve liste içeren örnek rapor üret."""
    doc = belge_olustur(sablon)
    kenar_bosluklari_ayarla(doc)
    kapak_sayfasi_ekle(doc, baslik, yazar, tarih)
    ustbilgi_altbilgi_ekle(doc, ustbilgi_metni=baslik)

    baslikli_bolum_ekle(doc, "Yonetici Ozeti",
                         "Bu belge word-author skill'i ile otomatik uretilmistir.")
    baslikli_bolum_ekle(doc, "Temel Bulgular", seviye=2)
    madde_listesi_ekle(doc, [
        "Konu 1: Onemli bir bulgu",
        "Konu 2: Ikinci onemli nokta",
        "Konu 3: Onerilen eylem",
    ])

    baslikli_bolum_ekle(doc, "Sayisal Ozet")
    tablo_ekle(doc, [
        ["Metrik", "Deger", "Hedef", "Durum"],
        ["Ornek A", "142", "150", "Iyi"],
        ["Ornek B", "87", "80", "Geçti"],
        ["Ornek C", "220", "200", "Geçti"],
    ])

    baslikli_bolum_ekle(doc, "Notlar")
    doc.add_paragraph(
        "Bu rapor ornek amaclidir. Gercek veri icin ilgili kaynaklari kullanin."
    )

    belge_kaydet(doc, dosya)
    belge_dogrula(dosya)


def main() -> None:
    ap = argparse.ArgumentParser(description="Word (DOCX) Belge Yardimcisi")
    ap.add_argument("--out", required=True, help="Cikti dosyasi (.docx)")
    ap.add_argument("--baslik", default="Rapor", help="Belge basligi")
    ap.add_argument("--yazar", default="", help="Yazar adi")
    ap.add_argument("--tarih", default=str(date.today()), help="Tarih")
    ap.add_argument("--template", default=None, help="Sablon .docx dosyasi")
    ap.add_argument("--tablo", default=None, help="Tablo verisi (.json dosyasi)")
    ap.add_argument("--tablo-baslik", default="", help="Tablo ustune baslik")
    ap.add_argument("--verify", action="store_true", help="Kaydedilen belgeyi dogrula")
    args = ap.parse_args()

    _require_docx()

    if args.tablo:
        try:
            with open(args.tablo, encoding="utf-8") as f:
                veri = json.load(f)
        except FileNotFoundError:
            print(f"HATA: JSON dosyası bulunamadı: {args.tablo}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as exc:
            print(f"HATA: Geçersiz JSON: {exc}", file=sys.stderr)
            sys.exit(1)
        doc = belge_olustur(args.template)
        kenar_bosluklari_ayarla(doc)
        kapak_sayfasi_ekle(doc, args.baslik, args.yazar, args.tarih)
        json_tablo_ekle(doc, veri, tablo_baslik=args.tablo_baslik)
        belge_kaydet(doc, args.out)
    else:
        ornek_rapor_uret(args.out, args.baslik, args.yazar, args.tarih, args.template)

    if args.verify:
        belge_dogrula(args.out)


if __name__ == "__main__":
    main()

