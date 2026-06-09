#!/usr/bin/env python3
"""
UYAP / e-Bilirkişi Entegrasyon Araçları

Türkiye Adalet Bakanlığı Ulusal Yargı Ağı Projesi (UYAP) entegrasyonu için
beş araç sağlar:

  1. uyap_login           — e-Devlet Mobil İmza OAuth2 akışıyla UYAP'a giriş yapar
  2. uyap_device_check    — ADB üzerinden mobil cihazda UYAP uygulamalarını kontrol eder
  3. uyap_query           — UYAP REST API'sine istek gönderir (oturum cookie'leriyle)
  4. uyap_document_process— UYAP belgelerini işler / metni çıkarır / özetler
  5. uyap_bilirkisi_track — Bilirkişi atamalarını yerel depoda takip eder

OAuth2 Akışı (uyap_login):
  action="initiate"  — TC no ve telefon ile mobil imza isteği başlatır
  action="complete"  — İmzalama sonrası oturumu tamamlar ve kaydeder

Ortam Değişkenleri:
  UYAP_API_BASE_URL  — UYAP servis taban URL'si (varsayılan: https://bilirkisi.uyap.gov.tr)
  UYAP_API_TOKEN     — (isteğe bağlı) Bearer token; yoksa oturum cookie'leri kullanılır
"""

import html
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# UYAP / e-Devlet OAuth2 Sabitleri
# ---------------------------------------------------------------------------

_OAUTH_CLIENT_ID  = "74dba0a0-ef79-11e5-a837-0800200c9a66"
_OAUTH_STATE      = "1527"
_AUTH_BASE        = "https://giris.turkiye.gov.tr"
_BILIRKISI_BASE   = "https://bilirkisi.uyap.gov.tr"
_REDIRECT_URI     = f"{_BILIRKISI_BASE}/login.uyap"

_LOGIN_URL = (
    f"{_AUTH_BASE}/Giris/Mobil-Imza"
    f"?oauthClientId={_OAUTH_CLIENT_ID}"
    "&continue=" + (
        "https%3A%2F%2Fgiris.turkiye.gov.tr%2FOAuth2AuthorizationServer"
        "%2FAuthorizationController%3Fresponse_type%3Dcode%26scope%3D"
        "Kimlik-Dogrula%253BAd-Soyad%26redirect_uri%3D"
        "https%253A%252F%252Fbilirkisi.uyap.gov.tr%252Flogin.uyap"
        f"%26client_id%3D{_OAUTH_CLIENT_ID}"
        f"%26state%3D{_OAUTH_STATE}%26loginTypeIndex%3D2"
    )
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hermes_dir() -> Path:
    base = Path(os.environ.get("HERMES_DATA_DIR", Path.home() / ".hermes"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _session_path() -> Path:
    return _hermes_dir() / "uyap_session.json"


def _login_state_path() -> Path:
    return _hermes_dir() / "uyap_login_state.json"


def _credentials_path() -> Path:
    return _hermes_dir() / "uyap_credentials.json"


def _load_credentials() -> Dict[str, str]:
    path = _credentials_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_session() -> Optional[Dict[str, Any]]:
    path = _session_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_session(data: Dict[str, Any]) -> None:
    _session_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _bilirkisi_store_path() -> Path:
    return _hermes_dir() / "uyap_bilirkisi.json"


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


def _make_session():
    """Oturum cookie jar'ı ve özel başlıkları olan requests.Session döndürür."""
    import requests
    s = requests.Session()
    s.headers.update(_DEFAULT_HEADERS)
    return s


def _extract_hidden_fields(html_text: str) -> Dict[str, str]:
    """HTML formundaki gizli alanları çıkarır."""
    return {
        m.group(1): html.unescape(m.group(2))
        for m in re.finditer(
            r'<input[^>]+type=["\']hidden["\'][^>]+name=["\']([^"\']+)["\'][^>]+value=["\']([^"\']*)["\']',
            html_text, re.IGNORECASE
        )
    }


def _extract_form_action(html_text: str, fallback: str = "") -> str:
    """HTML formunun action URL'sini çıkarır."""
    m = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    return html.unescape(m.group(1)) if m else fallback


# ---------------------------------------------------------------------------
# 0. uyap_browser_login  —  Playwright ile gerçek tarayıcı kontrolü
# ---------------------------------------------------------------------------

def _browser_state_path() -> Path:
    return _hermes_dir() / "uyap_browser_state.json"


def uyap_browser_login(phase: str = "start", timeout: int = 90) -> str:
    """
    İki aşamalı Playwright login.

    phase='start'    — Form doldurur, İmzala tıklar, tarayıcı cookie'lerini kaydeder ve kapanır.
                       Telefona imza isteği gönderilir. Bitmez, beklemez.
    phase='complete' — Kaydedilmiş cookie'lerle tekrar açar, imzanın tamamlanmasını bekler (timeout sn).
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return tool_error("Playwright kurulu değil. 'pip install playwright && playwright install chromium' çalıştırın.")

    _LAUNCH_ARGS = [
        "--no-sandbox", "--disable-dev-shm-usage",
        "--ignore-certificate-errors", "--ignore-ssl-errors",
    ]
    _UA = ("Mozilla/5.0 (Linux; Android 13; SM-G991B) "
           "AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/120.0.0.0 Mobile Safari/537.36")

    # ------------------------------------------------------------------
    if phase == "start":
        creds = _load_credentials()
        tc_no = creds.get("tc_no", "")
        telefon = creds.get("telefon", "")
        operator = creds.get("operator", "turkcell").lower()
        if not tc_no or not telefon:
            return tool_error("Kimlik bilgileri eksik (~/.hermes/uyap_credentials.json).")
        tel = re.sub(r"[^0-9]", "", telefon)
        if tel.startswith("90"): tel = tel[2:]
        if tel.startswith("0"): tel = tel[1:]
        _OP_MAP = {"turkcell": "1", "türkcell": "1", "vodafone": "2",
                   "turktelekom": "3", "türktelekom": "3", "tt": "3"}
        gsmtype_val = _OP_MAP.get(operator, "1")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            ctx = browser.new_context(user_agent=_UA, locale="tr-TR", ignore_https_errors=True)
            page = ctx.new_page()
            try:
                page.goto(_LOGIN_URL, timeout=20_000, wait_until="domcontentloaded")
                page.fill('input[name="tridField"]', tc_no)
                page.fill('input[name="gsmField"]', tel)
                page.check(f'input[name="gsmtype"][value="{gsmtype_val}"]')
                page.click('button[name="submitButton"]')
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
                # Sözleşme → İmzala
                try:
                    page.click('button[value="İmzala"]', timeout=8_000)
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except PWTimeout:
                    pass
                # imzaBasladi sayfasındayız — cookie'leri kaydet
                all_cookies = ctx.cookies()
                form_url = page.url
                # Gizli alanları da kaydet
                hidden: Dict[str, str] = {}
                for el in page.query_selector_all('input[type="hidden"]'):
                    n = el.get_attribute("name") or ""
                    v = el.get_attribute("value") or ""
                    if n:
                        hidden[n] = v
                state = {
                    "cookies": [{"name": c["name"], "value": c["value"],
                                 "domain": c.get("domain",""), "path": c.get("path","/")}
                                for c in all_cookies],
                    "form_url": form_url,
                    "hidden": hidden,
                    "baslatma_zamani": _ts(),
                }
                _browser_state_path().write_text(
                    json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            finally:
                browser.close()

        return json.dumps({
            "durum": "imza_bekleniyor",
            "mesaj": (
                "Mobil imza isteği gönderildi. Telefonunuzu kontrol edin ve imzayı onaylayın. "
                "Onayladıktan sonra uyap_browser_login(phase='complete') çağırın."
            ),
            "form_url": form_url,
            "hidden_action": hidden.get("actionName", ""),
            "cookie_sayisi": len(all_cookies),
            "baslatma_zamani": state["baslatma_zamani"],
        }, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    elif phase == "complete":
        state_file = _browser_state_path()
        if not state_file.exists():
            return tool_error("Önce phase='start' ile imza isteği başlatın.")
        state = json.loads(state_file.read_text(encoding="utf-8"))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            ctx = browser.new_context(user_agent=_UA, locale="tr-TR", ignore_https_errors=True)
            # Kaydedilmiş cookie'leri yükle
            for c in state.get("cookies", []):
                try:
                    ctx.add_cookies([{
                        "name": c["name"], "value": c["value"],
                        "domain": c.get("domain", "giris.turkiye.gov.tr"),
                        "path": c.get("path", "/"),
                    }])
                except Exception:
                    pass
            page = ctx.new_page()
            final_url = state.get("form_url", _LOGIN_URL)
            bilirkisi_cookies: Dict[str, str] = {}
            adalet_auth = ""
            try:
                # İmzaBasladi sayfasına git — JS kendi polling'ini yapar
                page.goto(state["form_url"], timeout=15_000, wait_until="domcontentloaded")
                try:
                    page.wait_for_url("**bilirkisi.uyap.gov.tr**", timeout=timeout * 1000)
                    final_url = page.url
                except PWTimeout:
                    final_url = page.url

                all_cookies = ctx.cookies()
                bilirkisi_cookies = {
                    c["name"]: c["value"] for c in all_cookies
                    if "bilirkisi.uyap.gov.tr" in c.get("domain", "")
                }
                if not bilirkisi_cookies:
                    bilirkisi_cookies = {c["name"]: c["value"] for c in all_cookies}
                try:
                    adalet_auth = page.evaluate("localStorage.getItem('AdaletAuth') || ''")
                except Exception:
                    pass
            finally:
                browser.close()

        if _BILIRKISI_BASE not in (final_url or ""):
            return json.dumps({
                "durum": "henuz_imzalanmadi",
                "mesaj": "Oturum tamamlanamadı. İmzaladıktan sonra tekrar deneyin.",
                "son_url": final_url,
            }, ensure_ascii=False)

        session_data: Dict[str, Any] = {
            "cookies": bilirkisi_cookies,
            "base_url": _BILIRKISI_BASE,
            "giris_zamani": _ts(),
            "kaynak": "playwright",
            "son_url": final_url,
        }
        if adalet_auth:
            session_data["adalet_auth"] = adalet_auth
        _save_session(session_data)
        state_file.unlink(missing_ok=True)
        return json.dumps({
            "durum": "basarili",
            "mesaj": "UYAP e-Bilirkişi oturumu kuruldu ve kaydedildi.",
            "son_url": final_url,
            "cookie_sayisi": len(bilirkisi_cookies),
            "adalet_auth_kayitli": bool(adalet_auth),
            "giris_zamani": session_data["giris_zamani"],
        }, ensure_ascii=False, indent=2)

    return tool_error(f"Bilinmeyen phase: '{phase}'. Geçerliler: start, complete")


# ---------------------------------------------------------------------------
# 1. uyap_login  —  e-Devlet Mobil İmza OAuth2 akışı
# ---------------------------------------------------------------------------

def uyap_login(
    action: str = "initiate",
    tc_no: Optional[str] = None,
    telefon: Optional[str] = None,
    redirect_url: Optional[str] = None,
    auth_code: Optional[str] = None,
    timeout: int = 120,
    cookies_raw: Optional[str] = None,
    adalet_auth: Optional[str] = None,
) -> str:
    """
    e-Devlet Mobil İmza ile UYAP e-Bilirkişi portalına giriş yapar.

    İki adımlı akış:
      action="initiate"  — Mobil imza isteği başlatır (telefona imza gelir)
      action="complete"  — İmzalama tamamlandıktan sonra oturumu kaydeder
    """
    import requests
    from urllib.parse import urlparse, parse_qs, urljoin

    if action == "initiate":
        # Kayıtlı kimlik bilgilerini yükle (parametre verilmemişse)
        creds = _load_credentials()
        if not tc_no:
            tc_no = creds.get("tc_no")
        if not telefon:
            op = creds.get("operator", "")
            telefon = (creds.get("telefon", "") + (" " + op if op else "")).strip()

        if not tc_no:
            return tool_error("'initiate' için tc_no gerekli (veya ~/.hermes/uyap_credentials.json kaydedin).")
        if not telefon:
            return tool_error("'initiate' için telefon gerekli (veya ~/.hermes/uyap_credentials.json kaydedin).")

        # Telefon numarasını normalize et (0 ile başlıyorsa kaldır)
        tel = re.sub(r"[^0-9]", "", telefon)
        if tel.startswith("90"):
            tel = tel[2:]
        if tel.startswith("0"):
            tel = tel[1:]  # 5XXXXXXXXX formatına getir

        sess = _make_session()

        # 1. Giriş sayfasını al — oturum çerezi ve CSRF token için
        try:
            r = sess.get(_LOGIN_URL, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            return tool_error(f"Giriş sayfası açılamadı: {exc}")

        # Operatör → gsmtype (1=Türkcell, 2=Vodafone, 3=Türk Telekom)
        _OP_MAP = {"turkcell": "1", "türkcell": "1", "vodafone": "2",
                   "turktelekom": "3", "türktelekom": "3", "tt": "3"}
        creds_op = _load_credentials().get("operator", "turkcell").lower()
        gsmtype_val = _OP_MAP.get(creds_op, "1")
        # Ayrıca telefon stringinden de tespit et
        for op_key, op_val in _OP_MAP.items():
            if op_key in telefon.lower().replace(" ", ""):
                gsmtype_val = op_val
                break

        form_url = _LOGIN_URL

        def _get_hidden(html_text: str) -> Dict[str, str]:
            result: Dict[str, str] = {}
            for m in re.finditer(
                r'<input[^>]+type=["\'\s]*hidden["\'\s][^>]*/?>',
                html_text, re.IGNORECASE
            ):
                nm = re.search(r'name=["\']([^"\']+)["\']', m.group(0))
                vl = re.search(r'value=["\']([^"\']*)["\']', m.group(0))
                if nm:
                    result[nm.group(1)] = vl.group(1) if vl else ""
            return result

        # ADIM 1 → Sözleşme sayfasını al (TC + telefon gönder)
        hidden1 = _get_hidden(r.text)
        try:
            r2 = sess.post(form_url, data={
                **hidden1,
                "tridField": tc_no,
                "gsmField": tel,
                "gsmtype": gsmtype_val,
                "submitButton": "Devam Et",
            }, timeout=25, allow_redirects=True)
        except Exception as exc:
            return tool_error(f"Adım1 POST hatası: {exc}")

        # Hata kontrolü (TC/telefon hatalı vs.)
        err_m = re.search(
            r'(?:id|class)=["\'][^"\']*(?:form-error|error-msg|alert-danger)[^"\']*["\'][^>]*>\s*([^<]{5,300})',
            r2.text, re.IGNORECASE
        )
        if err_m:
            return tool_error(f"Giriş hatası: {err_m.group(1).strip()}")

        # ADIM 2 → Sözleşmeyi onayla (actionName=imzala)
        hidden2 = _get_hidden(r2.text)
        if "imzala" not in hidden2.get("actionName", "").lower():
            # Sayfa beklenmedik bir durumda
            text_preview = re.sub(r"<[^>]+>", " ", r2.text)
            text_preview = re.sub(r"\s+", " ", text_preview).strip()[:300]
            return tool_error(f"Beklenmeyen sayfa adımı. Önizleme: {text_preview}")

        try:
            r3 = sess.post(form_url, data={
                **hidden2,
                "submitButton": "İmzala",
            }, timeout=30, allow_redirects=True)
        except Exception as exc:
            return tool_error(f"Adım2 POST hatası (sözleşme): {exc}")

        # ADIM 3 → İmza isteği gönderildi mi?
        hidden3 = _get_hidden(r3.text)
        r3_text_clean = re.sub(r"<[^>]+>", " ", r3.text)
        r3_text_clean = re.sub(r"\s+", " ", r3_text_clean).strip()

        success_phrases = ["operatörden cevap bekleniyor", "imzabasladi", "bekliyor", "sign request"]
        basarili = (
            "imzaBasladi" in hidden3.get("actionName", "")
            or any(p in r3_text_clean.lower() for p in success_phrases)
        )

        # Oturum durumunu kaydet
        state_data = {
            "cookies": {c.name: c.value for c in sess.cookies},
            "form_url": form_url,
            "hidden3": hidden3,
            "tc_no_masked": tc_no[:3] + "****" + tc_no[-4:],
            "telefon_masked": tel[:3] + "****" + tel[-2:],
            "gsmtype": gsmtype_val,
            "zaman": _ts(),
        }
        _login_state_path().write_text(
            json.dumps(state_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if not basarili:
            return tool_error(
                f"İmza isteği gönderilemedi. Sayfa: {r3_text_clean[:200]}"
            )

        return json.dumps({
            "durum": "bekleniyor",
            "mesaj": "Mobil imza isteği Türkcell'e gönderildi. Telefonunuzu kontrol edin.",
            "sonraki_adim": (
                "Telefona gelen imza isteğini onaylayın, "
                "ardından uyap_login action='complete' ile oturumu tamamlayın."
            ),
            "istek_zamani": _ts(),
        }, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    elif action == "save_cookies":
        # Tarayıcı oturumundan cookie'leri doğrudan kaydet
        # cookies_raw: "JSESSIONID=xxx; TS01234=yyy" formatında ham cookie string
        cookies_dict: Dict[str, str] = {}

        if cookies_raw:
            for part in cookies_raw.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies_dict[k.strip()] = v.strip()
        elif adalet_auth:
            pass  # Sadece token kaydedilecek

        if not cookies_dict and not adalet_auth:
            return tool_error(
                "cookies_raw veya adalet_auth parametresi gerekli. "
                "Tarayıcı adres çubuğuna şunu yazın: javascript:alert(document.cookie)"
            )

        session_data: Dict[str, Any] = {
            "cookies": cookies_dict,
            "base_url": _BILIRKISI_BASE,
            "giris_zamani": _ts(),
            "kaynak": "manuel_tarayici",
        }
        if adalet_auth:
            session_data["adalet_auth"] = adalet_auth

        _save_session(session_data)
        return json.dumps({
            "durum": "basarili",
            "mesaj": "Tarayıcı oturum çerezleri kaydedildi. uyap_query ile portala erişebilirsiniz.",
            "cookie_sayisi": len(cookies_dict),
            "adalet_auth_kayitli": bool(adalet_auth),
            "giris_zamani": session_data["giris_zamani"],
        }, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    elif action == "complete":
        import requests
        from urllib.parse import urlparse, parse_qs

        # Kaydedilmiş login state'i yükle
        state_file = _login_state_path()
        if not state_file.exists():
            return tool_error("Önce action='initiate' ile giriş başlatın.")
        state = json.loads(state_file.read_text(encoding="utf-8"))

        # OAuth kodu doğrudan verilmişse: kısa yoldan oturumu kur
        code = auth_code
        if not code and redirect_url:
            qs = parse_qs(urlparse(redirect_url).query)
            code = qs.get("code", [None])[0]

        sess = _make_session()
        auth_domain = urlparse(_AUTH_BASE).netloc
        for name, value in state.get("cookies", {}).items():
            sess.cookies.set(name, value, domain=auth_domain)

        _BASE_URL   = f"{_AUTH_BASE}/Giris/Mobil-Imza"
        _AJAX1      = f"{_AUTH_BASE}/Giris/Mobil-Imza?actionName=ajaximzaBaslangicKontrol"
        _AJAX2      = f"{_AUTH_BASE}/Giris/Mobil-Imza?actionName=ajaximzaKontrol"
        _form_ref   = state.get("form_url", _LOGIN_URL)
        _post_hdrs  = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": _form_ref,
            "Origin": _AUTH_BASE,
        }

        if code:
            # OAuth kodu zaten var — doğrudan callback'e git
            r_cb = sess.get(
                f"{_REDIRECT_URI}?code={code}&state={_OAUTH_STATE}",
                timeout=30, allow_redirects=True,
            )
            if _BILIRKISI_BASE in r_cb.url:
                ck = {c.name: c.value for c in sess.cookies}
                _save_session({"cookies": ck, "base_url": _BILIRKISI_BASE,
                               "giris_zamani": _ts(), "auth_code": code})
                state_file.unlink(missing_ok=True)
                return json.dumps({"durum": "basarili", "son_url": r_cb.url,
                                   "cookie_sayisi": len(ck)}, ensure_ascii=False, indent=2)
            return tool_error(f"Callback başarısız: {r_cb.url}")

        # ── FAZ 1: ajaximzaBaslangicKontrol — rc=3 (HASHPAGE) bekle ──
        # rc=0 → henüz Türkcell'e ulaşmadı
        # rc=3 → signing request telefona gitti → FAZ 1 tamamlanmış sayılır
        # rc=2 → hata (rate limit, zaman aşımı)
        faz1_deadline = time.monotonic() + min(timeout, 30)
        rc1, rd1 = 0, {}
        while time.monotonic() < faz1_deadline:
            try:
                rd1 = sess.get(_AJAX1, timeout=6).json()
                rc1 = rd1.get("resultCode", -1)
            except Exception:
                rc1 = -1
            if rc1 != 0:
                break
            time.sleep(3)

        if rc1 == 0:
            return json.dumps({
                "durum": "bekleniyor",
                "mesaj": "Mobil imza isteği Türkcell'e henüz ulaşmadı. Biraz bekleyip tekrar deneyin.",
                "ajax1": rd1,
            }, ensure_ascii=False)

        if rc1 == 2:
            return json.dumps({
                "durum": "hata_faz1",
                "mesaj": "Türkcell imza isteğini reddetti (hız sınırı veya çakışma). "
                         "Birkaç dakika bekleyip yeniden initiate yapın.",
                "ajax1": rd1,
            }, ensure_ascii=False)

        # rc1=3 (HASHPAGE): form submit → hash bekleme sayfasına geç
        rh = sess.post(_BASE_URL, data={"actionName": "imzaBasladi"},
                       timeout=20, allow_redirects=True, headers=_post_hdrs)
        hash_hidden = _extract_hidden_fields(rh.text)
        hash_ref    = rh.url

        # ── FAZ 2: ajaximzaKontrol — kullanıcı PIN girene kadar bekle ──
        # rc=0 veya rc=3 → henüz imzalanmadı (beklemeye devam)
        # rc=1 → başarılı imza
        # rc=2 → hata / iptal
        faz2_deadline = time.monotonic() + max(timeout - 30, 60)
        rc2, rd2 = 3, {}
        while time.monotonic() < faz2_deadline:
            try:
                rd2 = sess.get(_AJAX2, timeout=6).json()
                rc2 = rd2.get("resultCode", -1)
            except Exception:
                rc2 = 3  # JSON değilse bekle
            if rc2 not in (0, 3):
                break
            time.sleep(3)

        if rc2 in (0, 3):
            return json.dumps({
                "durum": "bekleniyor",
                "mesaj": "İmza süresi doldu. Telefonda imza isteği görüyorsanız daha hızlı onaylayın.",
                "ajax2": rd2,
            }, ensure_ascii=False)

        # rc2=1 (başarı) veya rc2=2 (hata) → final form submit
        # hash_hidden'da actionName=mobilimzagiris var
        _ph = {**hash_hidden}
        rfinal = sess.post(_BASE_URL, data=_ph, timeout=30, allow_redirects=True,
                           headers={**_post_hdrs, "Referer": hash_ref})

        if _BILIRKISI_BASE in rfinal.url:
            ck = {c.name: c.value for c in sess.cookies}
            _save_session({"cookies": ck, "base_url": _BILIRKISI_BASE,
                           "giris_zamani": _ts(), "faz2_rc": rc2})
            state_file.unlink(missing_ok=True)
            return json.dumps({
                "durum": "basarili",
                "mesaj": "UYAP e-Bilirkişi oturumu başarıyla kuruldu.",
                "son_url": rfinal.url,
                "cookie_sayisi": len(ck),
                "giris_zamani": _ts(),
            }, ensure_ascii=False, indent=2)

        # Başarısız — hata detayını dön
        txt = re.sub(r"<[^>]+>", " ", rfinal.text)
        txt = re.sub(r"\s+", " ", txt).strip()[:300]
        return json.dumps({
            "durum": "hata_faz2",
            "mesaj": "İmzalama tamamlandı ama UYAP oturumu kurulamadı.",
            "faz2_rc": rc2,
            "son_url": rfinal.url,
            "hash_hidden": hash_hidden,
            "sayfa_ozeti": txt,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    elif action == "status":
        sess_data = _load_session()
        if not sess_data:
            return json.dumps({"durum": "oturum_yok", "mesaj": "Aktif UYAP oturumu yok."})
        return json.dumps({
            "durum": "aktif",
            "giris_zamani": sess_data.get("giris_zamani"),
            "base_url": sess_data.get("base_url"),
            "cookie_sayisi": len(sess_data.get("cookies", {})),
        }, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    elif action == "logout":
        _session_path().unlink(missing_ok=True)
        _login_state_path().unlink(missing_ok=True)
        return json.dumps({"durum": "basarili", "mesaj": "Oturum silindi."}, ensure_ascii=False)

    return tool_error(f"Bilinmeyen action: '{action}'. Geçerliler: initiate, complete, status, logout")


# ---------------------------------------------------------------------------
# 2. uyap_device_check
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
# 3. uyap_query
# ---------------------------------------------------------------------------

def uyap_query(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> str:
    """UYAP REST API'sine HTTP isteği gönderir. Kayıtlı oturumu kullanır."""
    import requests as _req

    base_url = os.environ.get("UYAP_API_BASE_URL", _BILIRKISI_BASE).rstrip("/")
    token = os.environ.get("UYAP_API_TOKEN", "")

    url = f"{base_url}/{endpoint.lstrip('/')}"
    method = method.upper()

    sess = _make_session()
    sess_data = _load_session()
    adalet_auth_token = ""
    if sess_data:
        adalet_auth_token = sess_data.pop("adalet_auth", "") or ""
        for name, value in sess_data.get("cookies", {}).items():
            sess.cookies.set(name, value)

    headers: Dict[str, str] = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if adalet_auth_token:
        headers["AdaletAuth"] = adalet_auth_token
        headers["AdaletAuthAccess"] = adalet_auth_token

    try:
        t0 = time.monotonic()
        if method == "GET":
            resp = sess.get(url, params=params, headers=headers, timeout=timeout)
        else:
            resp = sess.request(method, url, json=params, headers=headers, timeout=timeout)
        elapsed = round(time.monotonic() - t0, 3)

        if resp.status_code in (401, 403) or (resp.url and "giris" in resp.url.lower()):
            return json.dumps({
                "durum": "oturum_suresi_doldu",
                "mesaj": "UYAP oturumu sona erdi. uyap_login action='initiate' ile tekrar giriş yapın.",
                "http_kodu": resp.status_code,
            }, ensure_ascii=False)

        try:
            veri = resp.json()
        except Exception:
            veri = resp.text[:10_000]

        return json.dumps({
            "durum": "basarili",
            "http_kodu": resp.status_code,
            "sure_sn": elapsed,
            "url": resp.url,
            "yontem": method,
            "veri": veri,
        }, ensure_ascii=False, indent=2)

    except _req.exceptions.ConnectionError as exc:
        return tool_error(f"Bağlantı hatası ({url}): {exc}")
    except _req.exceptions.Timeout:
        return tool_error(f"Zaman aşımı ({timeout}s): {url}")
    except Exception as exc:
        return tool_error(f"İstek hatası: {exc}")


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

_LOGIN_SCHEMA = {
    "name": "uyap_login",
    "description": (
        "e-Devlet Mobil İmza OAuth2 akışıyla UYAP e-Bilirkişi portalına giriş yapar.\n\n"
        "Kullanım:\n"
        "• action='initiate' → TC no + telefon ile Türkcell imza isteği başlatır\n"
        "• action='complete' → imzalama sonrası AJAX polling ile oturumu tamamlar\n"
        "• action='save_cookies' → telefon tarayıcısındaki aktif oturumu kaydeder "
        "(cookies_raw: tarayıcı adres çubuğuna 'javascript:alert(document.cookie)' "
        "yazınca çıkan metni yapıştırın)\n"
        "• action='status' → mevcut oturum durumunu gösterir\n"
        "• action='logout' → kaydedilmiş oturumu siler"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["initiate", "complete", "save_cookies", "status", "logout"],
                "description": "Yapılacak işlem.",
            },
            "tc_no": {
                "type": "string",
                "description": "TC kimlik numarası (11 hane). Sadece 'initiate' için gerekli.",
            },
            "telefon": {
                "type": "string",
                "description": "GSM telefon numarası (Türkcell/Vodafone/TT). Sadece 'initiate' için gerekli.",
            },
            "redirect_url": {
                "type": "string",
                "description": (
                    "'complete' için: imzalama sonrası yönlendirilen URL "
                    "(ör. https://bilirkisi.uyap.gov.tr/login.uyap?code=...)."
                ),
            },
            "auth_code": {
                "type": "string",
                "description": "'complete' için: OAuth2 yetkilendirme kodu.",
            },
            "cookies_raw": {
                "type": "string",
                "description": (
                    "'save_cookies' için: tarayıcıdan alınan ham cookie dizesi. "
                    "Chrome'da bilirkisi.uyap.gov.tr açıkken adres çubuğuna "
                    "'javascript:alert(document.cookie)' yazıp çıkan metni buraya yapıştırın."
                ),
            },
            "adalet_auth": {
                "type": "string",
                "description": (
                    "UYAP Adalet Auth JWT token'ı. "
                    "Tarayıcı konsolunda: localStorage.getItem('AdaletAuth')"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Saniye cinsinden bekleme süresi. Varsayılan: 120.",
                "default": 120,
            },
        },
        "required": ["action"],

    },
}

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
        "UYAP e-Bilirkişi portalına HTTP isteği gönderir (taban: bilirkisi.uyap.gov.tr). "
        "Kayıtlı oturum çerezlerini otomatik ekler.\n\n"
        "Bilinen endpoint'ler:\n"
        "• kullanici_bilgileri.uyap — oturum açmış kullanıcı bilgileri (GET)\n"
        "• menuListesiGetir.ajx — menü listesi (GET)\n"
        "• anasayfa.ajx — ana sayfa verileri (GET)\n"
        "• portal_baslangic.uyap — portal başlangıç (GET ?param=user&value=b&login_type=tm)\n"
        "• kill_session.uyap — oturum sonlandır (GET)\n"
        "• islistesiloader — iş listesi sayfasını aç (GET, tarayıcıda açar)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "description": (
                    "API yolu (ör. 'kullanici_bilgileri.uyap', 'menuListesiGetir.ajx'). "
                    "Başında / olabilir veya olmayabilir."
                )
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

_BROWSER_LOGIN_SCHEMA = {
    "name": "uyap_browser_login",
    "description": (
        "Playwright headless Chromium ile iki aşamalı UYAP girişi.\n\n"
        "• phase='start'    — Form doldurur, İmzala tıklar, browser kapanır, imza isteği gönderilir\n"
        "• phase='complete' — İmzaladıktan sonra çağırın; oturumu tamamlar ve kaydeder\n\n"
        "Kayıtlı kimlik bilgileri (~/.hermes/uyap_credentials.json) kullanılır. "
        "HttpOnly cookie'ler dahil tam oturum yakalanır."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["start", "complete"],
                "description": "start: imza isteği gönder. complete: imzaladıktan sonra oturumu bitir.",
                "default": "start",
            },
            "timeout": {
                "type": "integer",
                "description": "complete aşamasında imzalama bekleme süresi (saniye). Varsayılan: 90.",
                "default": 90,
            },
        },
        "required": []
    }
}

registry.register(
    name="uyap_browser_login",
    toolset="uyap",
    schema=_BROWSER_LOGIN_SCHEMA,
    handler=lambda args, **kw: uyap_browser_login(
        phase=args.get("phase", "start"),
        timeout=args.get("timeout", 90),
    ),
    check_fn=_check_uyap,
    emoji="🌐",
)

registry.register(
    name="uyap_login",
    toolset="uyap",
    schema=_LOGIN_SCHEMA,
    handler=lambda args, **kw: uyap_login(
        action=args.get("action", "initiate"),
        tc_no=args.get("tc_no"),
        telefon=args.get("telefon"),
        redirect_url=args.get("redirect_url"),
        auth_code=args.get("auth_code"),
        timeout=args.get("timeout", 120),
        cookies_raw=args.get("cookies_raw"),
        adalet_auth=args.get("adalet_auth"),
    ),
    check_fn=_check_uyap,
    emoji="🔐",
)

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
