#!/usr/bin/env python3
"""
UYAP / e-Bilirkişi Entegrasyon Araçları

Türkiye Adalet Bakanlığı Ulusal Yargı Ağı Projesi (UYAP) entegrasyonu için
dört araç sağlar:

  1. uyap_device_check    — ADB üzerinden mobil cihazda UYAP uygulamalarını kontrol eder
  2. uyap_query           — UYAP REST API'sine istek gönderir
  3. uyap_document_process— UYAP belgelerini işler / metni çıkarır / özetler
  4. uyap_bilirkisi_track — Bilirkişi atamalarını yerel depoda takip eder

Ortam Değişkenleri:
  UYAP_API_BASE_URL  — UYAP servis taban URL'si (ör. https://vatandas.uyap.gov.tr)
  UYAP_API_TOKEN     — Yetkilendirme token'ı (Bearer)
"""

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bilirkisi_store_path() -> Path:
    base = Path(os.environ.get("HERMES_DATA_DIR", Path.home() / ".hermes"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "uyap_bilirkisi.json"


def _load_bilirkisi_store() -> Dict[str, Any]:
    path = _bilirkisi_store_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"atamalar": [], "meta": {"version": "1", "son_guncelleme": _ts()}}


def _save_bilirkisi_store(store: Dict[str, Any]) -> None:
    store["meta"]["son_guncelleme"] = _ts()
    _bilirkisi_store_path().write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. uyap_device_check
# ---------------------------------------------------------------------------

UYAP_PAKETLER = {
    "tr.gov.adalet.uyap.mobiluyap": "Mobil UYAP",
    "tr.gov.adalet.uyap.ebilirkisi": "e-Bilirkişi",
    "tr.gov.adalet.uyap.vatandas": "UYAP Vatandaş",
    "tr.gov.adalet.uyap.avukat": "UYAP Avukat Portalı",
    "tr.gov.adalet.uyap.icra": "UYAP İcra",
}


def uyap_device_check(
    device_id: Optional[str] = None,
    extra_packages: Optional[List[str]] = None,
) -> str:
    """ADB üzerinden bağlı Android cihazda UYAP uygulamalarını kontrol eder."""

    # adb erişilebilir mi?
    try:
        probe = subprocess.run(
            ["adb", "version"], capture_output=True, text=True, timeout=5
        )
        if probe.returncode != 0:
            return tool_error("adb bulunamadı. Android SDK Platform-Tools kurulu olmalı.")
    except FileNotFoundError:
        return tool_error(
            "adb komutu bulunamadı. "
            "Android SDK Platform-Tools'u yükleyin ve PATH'e ekleyin."
        )

    # Bağlı cihazları listele
    devices_result = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=10
    )
    lines = [
        ln.strip()
        for ln in devices_result.stdout.splitlines()
        if ln.strip() and not ln.startswith("List of")
    ]
    connected = [
        ln.split()[0]
        for ln in lines
        if len(ln.split()) >= 2 and ln.split()[1] == "device"
    ]

    if not connected:
        return json.dumps({
            "durum": "hata",
            "mesaj": "Bağlı Android cihaz bulunamadı. USB hata ayıklamanın açık olduğundan emin olun.",
            "bagli_cihazlar": [],
        }, ensure_ascii=False)

    hedef = device_id if device_id else connected[0]
    if hedef not in connected:
        return tool_error(f"Cihaz bulunamadı: {hedef}. Bağlı cihazlar: {connected}")

    adb_prefix = ["adb", "-s", hedef]

    # Cihaz bilgisi
    def adb_prop(prop: str) -> str:
        r = subprocess.run(
            adb_prefix + ["shell", "getprop", prop],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip()

    cihaz_bilgi = {
        "seri": hedef,
        "model": adb_prop("ro.product.model"),
        "marka": adb_prop("ro.product.brand"),
        "android": adb_prop("ro.build.version.release"),
        "sdk": adb_prop("ro.build.version.sdk"),
    }

    # Paket kontrolü
    kontrol_listesi = dict(UYAP_PAKETLER)
    for p in (extra_packages or []):
        kontrol_listesi[p] = p

    pkg_list_r = subprocess.run(
        adb_prefix + ["shell", "pm", "list", "packages"],
        capture_output=True, text=True, timeout=15
    )
    yuklu_paketler = {
        ln.replace("package:", "").strip()
        for ln in pkg_list_r.stdout.splitlines()
        if ln.startswith("package:")
    }

    sonuclar = []
    for paket, isim in kontrol_listesi.items():
        yuklu = paket in yuklu_paketler
        bilgi: Dict[str, Any] = {
            "paket": paket,
            "isim": isim,
            "yuklu": yuklu,
        }
        if yuklu:
            # Uygulama sürümünü al
            ver_r = subprocess.run(
                adb_prefix + ["shell", "dumpsys", "package", paket],
                capture_output=True, text=True, timeout=10
            )
            for ln in ver_r.stdout.splitlines():
                if "versionName" in ln:
                    bilgi["surum"] = ln.strip().split("=", 1)[-1]
                    break
        sonuclar.append(bilgi)

    yuklu_sayisi = sum(1 for s in sonuclar if s["yuklu"])

    return json.dumps({
        "durum": "basarili",
        "cihaz": cihaz_bilgi,
        "kontrol_zamani": _ts(),
        "bagli_cihazlar": connected,
        "uygulamalar": sonuclar,
        "ozet": f"{yuklu_sayisi}/{len(sonuclar)} UYAP uygulaması kurulu",
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 2. uyap_query
# ---------------------------------------------------------------------------

def uyap_query(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> str:
    """UYAP REST API'sine istek gönderir."""

    base_url = os.environ.get("UYAP_API_BASE_URL", "").rstrip("/")
    token = os.environ.get("UYAP_API_TOKEN", "")

    if not base_url:
        return tool_error(
            "UYAP_API_BASE_URL ortam değişkeni tanımlı değil. "
            "Örnek: export UYAP_API_BASE_URL=https://vatandas.uyap.gov.tr"
        )

    try:
        import urllib.request
        import urllib.parse
        import urllib.error
    except ImportError:
        return tool_error("urllib modülü bulunamadı.")

    url = f"{base_url}/{endpoint.lstrip('/')}"
    method = method.upper()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "hermes-agent/uyap-integration",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body: Optional[bytes] = None
    if method == "GET" and params:
        qs = urllib.parse.urlencode(params)
        url = f"{url}?{qs}"
    elif params:
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = round(time.monotonic() - t0, 3)
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw

            return json.dumps({
                "durum": "basarili",
                "http_kodu": resp.status,
                "sure_sn": elapsed,
                "url": url,
                "yontem": method,
                "veri": data,
            }, ensure_ascii=False, indent=2)

    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return json.dumps({
            "durum": "hata",
            "http_kodu": exc.code,
            "mesaj": str(exc.reason),
            "url": url,
            "yanit": body_text[:2000],
        }, ensure_ascii=False)
    except urllib.error.URLError as exc:
        return tool_error(f"Bağlantı hatası ({url}): {exc.reason}")
    except TimeoutError:
        return tool_error(f"Zaman aşımı ({timeout}s): {url}")


# ---------------------------------------------------------------------------
# 3. uyap_document_process
# ---------------------------------------------------------------------------

def uyap_document_process(
    file_path: str,
    action: str = "extract_text",
    output_format: str = "text",
) -> str:
    """UYAP belgesini işler: metin çıkarma, metadata, tablo veya özet."""

    path = Path(file_path).expanduser()
    if not path.exists():
        return tool_error(f"Dosya bulunamadı: {file_path}")
    if not path.is_file():
        return tool_error(f"Belirtilen yol bir dosya değil: {file_path}")

    suffix = path.suffix.lower()
    valid_actions = {"extract_text", "parse_metadata", "extract_tables", "summarize"}
    if action not in valid_actions:
        return tool_error(
            f"Geçersiz işlem: '{action}'. "
            f"Geçerli seçenekler: {', '.join(sorted(valid_actions))}"
        )

    result: Dict[str, Any] = {
        "dosya": str(path),
        "uzanti": suffix,
        "boyut_kb": round(path.stat().st_size / 1024, 2),
        "islem": action,
        "zaman": _ts(),
    }

    # --- PDF ---
    if suffix == ".pdf":
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(path) as pdf:
                result["sayfa_sayisi"] = len(pdf.pages)

                if action == "extract_text":
                    pages_text = []
                    for i, pg in enumerate(pdf.pages, 1):
                        txt = pg.extract_text() or ""
                        pages_text.append({"sayfa": i, "metin": txt.strip()})
                    result["sayfalar"] = pages_text
                    result["toplam_karakter"] = sum(len(p["metin"]) for p in pages_text)

                elif action == "parse_metadata":
                    result["metadata"] = pdf.metadata or {}

                elif action == "extract_tables":
                    tables = []
                    for i, pg in enumerate(pdf.pages, 1):
                        for tbl in pg.extract_tables() or []:
                            tables.append({"sayfa": i, "tablo": tbl})
                    result["tablolar"] = tables
                    result["tablo_sayisi"] = len(tables)

                elif action == "summarize":
                    full_text = " ".join(
                        (pg.extract_text() or "") for pg in pdf.pages
                    ).strip()
                    words = full_text.split()
                    result["kelime_sayisi"] = len(words)
                    result["ilk_500_kelime"] = " ".join(words[:500])
                    result["son_guncelleme"] = pdf.metadata.get("ModDate", "")

        except ImportError:
            # pdfplumber yoksa ham bayt sayısını döndür
            result["uyari"] = "pdfplumber kurulu değil; tam işlem için 'pip install pdfplumber' çalıştırın."
            result["ham_boyut_kb"] = result["boyut_kb"]

    # --- Metin tabanlı dosyalar ---
    elif suffix in {".txt", ".xml", ".json", ".csv", ".html", ".htm"}:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return tool_error(f"Dosya okunamadı: {exc}")

        if action == "extract_text":
            result["metin"] = content[:50_000]
            result["toplam_karakter"] = len(content)

        elif action == "parse_metadata":
            result["karakter_sayisi"] = len(content)
            result["satir_sayisi"] = content.count("\n")
            result["kelime_sayisi"] = len(content.split())
            if suffix == ".json":
                try:
                    parsed = json.loads(content)
                    result["json_anahtarlari"] = (
                        list(parsed.keys()) if isinstance(parsed, dict) else None
                    )
                except json.JSONDecodeError:
                    result["json_gecerli"] = False

        elif action == "extract_tables":
            if suffix == ".csv":
                import csv, io
                reader = csv.reader(io.StringIO(content))
                rows = list(reader)
                result["satir_sayisi"] = len(rows)
                result["sutun_sayisi"] = max((len(r) for r in rows), default=0)
                result["ilk_10_satir"] = rows[:10]
            else:
                result["uyari"] = f"Tablo çıkarma {suffix} için desteklenmiyor."

        elif action == "summarize":
            words = content.split()
            result["kelime_sayisi"] = len(words)
            result["ilk_500_kelime"] = " ".join(words[:500])

    # --- Desteklenmeyen format ---
    else:
        result["uyari"] = (
            f"'{suffix}' formatı tam olarak desteklenmiyor. "
            "Desteklenen formatlar: .pdf, .txt, .xml, .json, .csv, .html"
        )
        result["ham_boyut_kb"] = result["boyut_kb"]

    if output_format == "markdown" and "sayfalar" in result:
        md_parts = [f"# {path.name}\n"]
        for pg in result.get("sayfalar", []):
            md_parts.append(f"## Sayfa {pg['sayfa']}\n{pg['metin']}\n")
        result["markdown"] = "\n".join(md_parts)

    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 4. uyap_bilirkisi_track
# ---------------------------------------------------------------------------

def uyap_bilirkisi_track(
    action: str,
    dosya_no: Optional[str] = None,
    bilirkisi_id: Optional[str] = None,
    durum: Optional[str] = None,
    notlar: Optional[str] = None,
    atama_tarihi: Optional[str] = None,
    teslim_tarihi: Optional[str] = None,
) -> str:
    """Bilirkişi atamalarını yerel depoda takip eder."""

    valid_actions = {"list", "add", "update", "query", "delete"}
    if action not in valid_actions:
        return tool_error(
            f"Geçersiz işlem: '{action}'. "
            f"Geçerli seçenekler: {', '.join(sorted(valid_actions))}"
        )

    store = _load_bilirkisi_store()
    atamalar: List[Dict[str, Any]] = store.get("atamalar", [])

    # --- list ---
    if action == "list":
        filtreli = atamalar
        if durum:
            filtreli = [a for a in atamalar if a.get("durum") == durum]
        if bilirkisi_id:
            filtreli = [a for a in filtreli if a.get("bilirkisi_id") == bilirkisi_id]
        return json.dumps({
            "durum": "basarili",
            "toplam": len(filtreli),
            "atamalar": filtreli,
        }, ensure_ascii=False, indent=2)

    # --- query ---
    if action == "query":
        if not dosya_no:
            return tool_error("'query' işlemi için 'dosya_no' gerekli.")
        bulunan = [a for a in atamalar if a.get("dosya_no") == dosya_no]
        return json.dumps({
            "durum": "basarili",
            "dosya_no": dosya_no,
            "atama_sayisi": len(bulunan),
            "atamalar": bulunan,
        }, ensure_ascii=False, indent=2)

    # --- add ---
    if action == "add":
        if not dosya_no:
            return tool_error("'add' işlemi için 'dosya_no' gerekli.")
        if not bilirkisi_id:
            return tool_error("'add' işlemi için 'bilirkisi_id' gerekli.")

        yeni: Dict[str, Any] = {
            "id": f"{dosya_no}_{bilirkisi_id}_{int(time.time())}",
            "dosya_no": dosya_no,
            "bilirkisi_id": bilirkisi_id,
            "durum": durum or "beklemede",
            "atama_tarihi": atama_tarihi or _ts(),
            "teslim_tarihi": teslim_tarihi or "",
            "notlar": notlar or "",
            "olusturma_zamani": _ts(),
            "guncelleme_zamani": _ts(),
        }
        atamalar.append(yeni)
        store["atamalar"] = atamalar
        _save_bilirkisi_store(store)
        return json.dumps({
            "durum": "basarili",
            "mesaj": "Atama eklendi.",
            "atama": yeni,
        }, ensure_ascii=False, indent=2)

    # --- update ---
    if action == "update":
        if not dosya_no or not bilirkisi_id:
            return tool_error("'update' için 'dosya_no' ve 'bilirkisi_id' gerekli.")
        guncellenen = []
        for atama in atamalar:
            if atama.get("dosya_no") == dosya_no and atama.get("bilirkisi_id") == bilirkisi_id:
                if durum:
                    atama["durum"] = durum
                if notlar:
                    atama["notlar"] = notlar
                if teslim_tarihi:
                    atama["teslim_tarihi"] = teslim_tarihi
                atama["guncelleme_zamani"] = _ts()
                guncellenen.append(atama)
        if not guncellenen:
            return tool_error(f"Atama bulunamadı: dosya={dosya_no}, bilirkisi={bilirkisi_id}")
        store["atamalar"] = atamalar
        _save_bilirkisi_store(store)
        return json.dumps({
            "durum": "basarili",
            "mesaj": f"{len(guncellenen)} atama güncellendi.",
            "atamalar": guncellenen,
        }, ensure_ascii=False, indent=2)

    # --- delete ---
    if action == "delete":
        if not dosya_no:
            return tool_error("'delete' için 'dosya_no' gerekli.")
        onceki = len(atamalar)
        atamalar = [
            a for a in atamalar
            if not (
                a.get("dosya_no") == dosya_no
                and (bilirkisi_id is None or a.get("bilirkisi_id") == bilirkisi_id)
            )
        ]
        silinen = onceki - len(atamalar)
        store["atamalar"] = atamalar
        _save_bilirkisi_store(store)
        return json.dumps({
            "durum": "basarili",
            "mesaj": f"{silinen} atama silindi.",
            "kalan_toplam": len(atamalar),
        }, ensure_ascii=False, indent=2)

    return tool_error(f"Bilinmeyen işlem: {action}")


# ---------------------------------------------------------------------------
# Şemalar
# ---------------------------------------------------------------------------

_DEVICE_CHECK_SCHEMA = {
    "name": "uyap_device_check",
    "description": (
        "ADB (Android Debug Bridge) üzerinden bağlı Android cihazda "
        "Mobil UYAP, e-Bilirkişi ve diğer UYAP uygulamalarının kurulu olup "
        "olmadığını kontrol eder. Cihaz bilgisi, kurulu uygulama listesi ve "
        "sürüm bilgisini döndürür."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "ADB cihaz seri numarası. Belirtilmezse ilk bağlı cihaz kullanılır."
            },
            "extra_packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Kontrol edilecek ek paket adları (ör. ['com.example.app'])."
            },
        },
        "required": []
    }
}

_QUERY_SCHEMA = {
    "name": "uyap_query",
    "description": (
        "UYAP REST API'sine HTTP isteği gönderir. "
        "UYAP_API_BASE_URL ve UYAP_API_TOKEN ortam değişkenlerini kullanır. "
        "Dosya sorgulama, dava bilgisi alma veya belge listesi çekme gibi "
        "UYAP servislerine erişim sağlar."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "description": "API yolu, taban URL'ye göre (ör. '/dosya/sorgula', '/bilirkisi/listesi')."
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "description": "HTTP yöntemi. Varsayılan: GET."
            },
            "params": {
                "type": "object",
                "description": "GET için sorgu parametreleri; POST/PUT için istek gövdesi."
            },
            "timeout": {
                "type": "integer",
                "description": "Saniye cinsinden zaman aşımı. Varsayılan: 30.",
                "default": 30
            }
        },
        "required": ["endpoint"]
    }
}

_DOCUMENT_SCHEMA = {
    "name": "uyap_document_process",
    "description": (
        "UYAP belgelerini işler. PDF, TXT, XML, JSON, CSV ve HTML formatlarını "
        "destekler. Metin çıkarma, metadata ayrıştırma, tablo çıkarma ve "
        "özet oluşturma işlemlerini gerçekleştirir."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "İşlenecek belgenin tam dosya yolu."
            },
            "action": {
                "type": "string",
                "enum": ["extract_text", "parse_metadata", "extract_tables", "summarize"],
                "description": (
                    "Yapılacak işlem: "
                    "extract_text=metin çıkar, "
                    "parse_metadata=üstveri oku, "
                    "extract_tables=tabloları çıkar, "
                    "summarize=özet oluştur."
                )
            },
            "output_format": {
                "type": "string",
                "enum": ["text", "json", "markdown"],
                "description": "Çıktı formatı. Varsayılan: text.",
                "default": "text"
            }
        },
        "required": ["file_path"]
    }
}

_BILIRKISI_SCHEMA = {
    "name": "uyap_bilirkisi_track",
    "description": (
        "Bilirkişi atamalarını yerel depoda yönetir ve takip eder. "
        "Atama ekleme, güncelleme, listeleme ve dosya numarasına göre sorgulama "
        "işlemlerini destekler. Veriler ~/.hermes/uyap_bilirkisi.json dosyasında saklanır."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "update", "query", "delete"],
                "description": (
                    "İşlem türü: "
                    "list=tüm atamaları listele, "
                    "add=yeni atama ekle, "
                    "update=atama güncelle, "
                    "query=dosya no ile sorgula, "
                    "delete=atama sil."
                )
            },
            "dosya_no": {
                "type": "string",
                "description": "Dava/dosya numarası (ör. '2024/1234')."
            },
            "bilirkisi_id": {
                "type": "string",
                "description": "Bilirkişi sicil/ID numarası."
            },
            "durum": {
                "type": "string",
                "enum": ["beklemede", "devam_ediyor", "tamamlandi", "iptal"],
                "description": "Atama durumu."
            },
            "notlar": {
                "type": "string",
                "description": "Serbest metin notlar."
            },
            "atama_tarihi": {
                "type": "string",
                "description": "Atama tarihi (ISO 8601, ör. '2024-03-01T10:00:00')."
            },
            "teslim_tarihi": {
                "type": "string",
                "description": "Rapor teslim tarihi (ISO 8601)."
            },
        },
        "required": ["action"]
    }
}


# ---------------------------------------------------------------------------
# Kayıt (Registry)
# ---------------------------------------------------------------------------

def _check_uyap() -> bool:
    """UYAP araçları her zaman kullanılabilir (ADB isteğe bağlı)."""
    return True


from tools.registry import registry, tool_error  # noqa: E402

registry.register(
    name="uyap_device_check",
    toolset="uyap",
    schema=_DEVICE_CHECK_SCHEMA,
    handler=lambda args, **kw: uyap_device_check(
        device_id=args.get("device_id"),
        extra_packages=args.get("extra_packages"),
    ),
    check_fn=_check_uyap,
    emoji="📱",
)

registry.register(
    name="uyap_query",
    toolset="uyap",
    schema=_QUERY_SCHEMA,
    handler=lambda args, **kw: uyap_query(
        endpoint=args["endpoint"],
        method=args.get("method", "GET"),
        params=args.get("params"),
        timeout=args.get("timeout", 30),
    ),
    check_fn=_check_uyap,
    emoji="🔌",
)

registry.register(
    name="uyap_document_process",
    toolset="uyap",
    schema=_DOCUMENT_SCHEMA,
    handler=lambda args, **kw: uyap_document_process(
        file_path=args["file_path"],
        action=args.get("action", "extract_text"),
        output_format=args.get("output_format", "text"),
    ),
    check_fn=_check_uyap,
    emoji="📄",
)

registry.register(
    name="uyap_bilirkisi_track",
    toolset="uyap",
    schema=_BILIRKISI_SCHEMA,
    handler=lambda args, **kw: uyap_bilirkisi_track(
        action=args["action"],
        dosya_no=args.get("dosya_no"),
        bilirkisi_id=args.get("bilirkisi_id"),
        durum=args.get("durum"),
        notlar=args.get("notlar"),
        atama_tarihi=args.get("atama_tarihi"),
        teslim_tarihi=args.get("teslim_tarihi"),
    ),
    check_fn=_check_uyap,
    emoji="⚖️",
)
