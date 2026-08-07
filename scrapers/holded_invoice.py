import calendar
import logging
import os
import re
import shutil
import time
from datetime import date, datetime
from typing import Optional

import requests
import undetected_chromedriver as uc
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HOLDEN_LOGIN_URL = "https://app.holded.com/login"
HOLDEN_INVOICES_URL = "https://app.holded.com/home#settings:/subscription/invoices"
HOLDEN_HOME_URL = "https://app.holded.com/home"
# Las facturas del plan viven en un drawer que la SPA monta desde el fragmento
# de la URL, no en una página propia.
SUBSCRIPTION_HASH = "settings:/subscription/invoices"
SUBSCRIPTION_PANEL_TEXT = "facturas de tu plan"
# Carpeta donde queda el PDF. Se puede apuntar a una carpeta sincronizada con
# Drive (rclone, Drive para escritorio...) vía HOLDED_DOWNLOAD_DIR.
DOWNLOAD_FOLDER = os.getenv("HOLDED_DOWNLOAD_DIR", "/app/data/holded_downloads").strip() or "/app/data/holded_downloads"
USER_DATA_DIR = "/app/data/holded_user_data"
LAST_RUN_FILE = "/app/data/holded_invoice_last_run.txt"
DEBUG_FOLDER = "/app/data/holded_debug"

# Botones de aceptación de los gestores de consentimiento más habituales.
# Se prueban antes que la búsqueda por texto porque son inequívocos.
COOKIE_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "#didomi-notice-agree-button",
    "button[data-testid='uc-accept-all-button']",
    ".cky-btn-accept",
    ".osano-cm-accept-all",
    "#cookiescript_accept",
    "#truste-consent-button",
    "#hs-eu-confirmation-button",
    ".cc-btn.cc-allow",
    "[data-cookiebanner='accept_button']",
    "button[aria-label*='ceptar' i]",
    "button[aria-label*='ccept' i]",
]

# Textos que deben coincidir de forma exacta: son tan cortos que como subcadena
# producen falsos positivos (p. ej. "ok" está dentro de "cookies").
COOKIE_TEXTS_EXACT = [
    "aceptar",
    "accept",
    "aceptar y cerrar",
    "entendido",
    "got it",
    "de acuerdo",
    "ok",
    "vale",
]

# Textos suficientemente largos como para buscarlos por subcadena.
COOKIE_TEXTS_CONTAINS = [
    "aceptar todas las cookies",
    "aceptar todas",
    "aceptar cookies",
    "aceptar todo",
    "permitir todas",
    "permitir todo",
    "accept all cookies",
    "accept all",
    "allow all cookies",
    "allow all",
    "allow cookies",
    "i agree",
    "estoy de acuerdo",
]

# Contenedores típicos de un aviso de cookies, usados para saber si queda banner.
COOKIE_BANNER_SELECTORS = [
    "#onetrust-banner-sdk",
    "#CybotCookiebotDialog",
    "#didomi-notice",
    ".cky-consent-container",
    ".osano-cm-window",
    "#cookiescript_injected",
    ".cc-window",
    "[id*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='consent' i]",
    "[class*='consent' i]",
]


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else value


def required_env(key: str) -> str:
    value = get_env(key)
    if not value:
        raise RuntimeError(f"La variable de entorno {key} es obligatoria.")
    return value


def build_driver(
    download_dir: str,
    user_data_dir: Optional[str],
    headless: bool = False,
    use_existing_chrome: bool = True,
    debugger_address: str = "127.0.0.1:9223",
) -> uc.Chrome:
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "/dev/null"
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-position=0,0")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=AudioServiceOutOfProcess,MediaSessionService")
    options.add_argument("--lang=es-ES")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    if use_existing_chrome and not headless:
        logger.info("Conectando al Chrome existente en %s", debugger_address)
        options.debugger_address = debugger_address
    else:
        if user_data_dir:
            options.add_argument(f"--user-data-dir={user_data_dir}")
        if headless:
            options.add_argument("--headless=new")
        else:
            options.add_argument("--remote-debugging-port=0")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    options.add_experimental_option("prefs", prefs)

    driver = uc.Chrome(options=options)
    return driver


def wait_for_element(driver, xpath: str, timeout: int = 30):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )


def wait_for_page_ready(driver, timeout: int = 30):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        logger.debug("La página no alcanzó readyState=complete en el tiempo esperado.")


def safe_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({ behavior: 'smooth', block: 'center' });", element)
        element.click()
        return True
    except ElementClickInterceptedException:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False
    except Exception:
        return False


def set_download_folder(driver, download_dir: str) -> None:
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": download_dir},
        )
        logger.info("Configurado directorio de descarga: %s", download_dir)
    except Exception as exc:
        logger.warning("No se pudo configurar el directorio de descarga por CDP: %s", exc)


def _xpath_lower(expr: str) -> str:
    return (
        "translate(normalize-space(%s), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑ', "
        "'abcdefghijklmnopqrstuvwxyzáéíóúüñ')" % expr
    )


def _text_xpaths(text: str, exact: bool) -> list:
    """XPaths para un texto de botón, restringidos a elementos realmente clicables.

    El segundo XPath admite span/div/label, pero solo si son hojas con texto corto:
    así se evita clicar el contenedor entero de la página (que también "contiene"
    el texto y cuyo click no hace nada).
    """
    labels = [_xpath_lower("."), _xpath_lower("@aria-label"), _xpath_lower("@value"), _xpath_lower("@title")]
    if exact:
        cond = " or ".join("%s = '%s'" % (label, text) for label in labels)
    else:
        cond = " or ".join("contains(%s, '%s')" % (label, text) for label in labels)
    clickable = "self::button or self::a or self::input or @role='button' or @onclick"
    leafish = "self::span or self::div or self::p or self::label"
    return [
        "//*[%s][%s]" % (clickable, cond),
        "//*[%s][not(*)][string-length(normalize-space(.)) < 40][%s]" % (leafish, cond),
    ]


def _is_interactable(element) -> bool:
    try:
        return element.is_displayed() and element.is_enabled()
    except Exception:
        return False


def _element_dismissed(element, timeout: float = 4.0) -> bool:
    """True si el elemento desaparece tras el click (señal de que sí se aceptó)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not element.is_displayed():
                return True
        except StaleElementReferenceException:
            return True
        except Exception:
            return True
        time.sleep(0.25)
    return False


def _click_consent(driver, element, description: str) -> bool:
    if not _is_interactable(element):
        return False
    if not safe_click(driver, element):
        return False
    if not _element_dismissed(element):
        logger.debug("Click en %s no hizo desaparecer el aviso; sigo buscando.", description)
        return False
    logger.info("Aceptadas cookies mediante %s", description)
    time.sleep(1.5)
    return True


def _accept_in_current_context(driver, where: str) -> bool:
    for selector in COOKIE_BUTTON_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for element in elements:
            if _click_consent(driver, element, "selector %r (%s)" % (selector, where)):
                return True

    for text, exact in [(t, True) for t in COOKIE_TEXTS_EXACT] + [(t, False) for t in COOKIE_TEXTS_CONTAINS]:
        for xpath in _text_xpaths(text.lower(), exact):
            try:
                elements = driver.find_elements(By.XPATH, xpath)
            except Exception:
                continue
            for element in elements:
                if _click_consent(driver, element, "texto %r (%s)" % (text, where)):
                    return True
    return False


def _accept_in_iframes(driver) -> bool:
    """Muchos CMP (Cookiebot, TrustArc...) pintan el aviso dentro de un iframe."""
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
    except Exception:
        return False
    for index, frame in enumerate(frames):
        try:
            driver.switch_to.frame(frame)
        except Exception:
            continue
        try:
            if _accept_in_current_context(driver, "iframe #%d" % index):
                return True
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    return False


_SHADOW_CLICK_JS = r"""
const texts = arguments[0];
const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
const visible = (el) => {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};
const nodes = [];
const walk = (root) => {
  for (const el of root.querySelectorAll('*')) {
    nodes.push(el);
    if (el.shadowRoot) walk(el.shadowRoot);
  }
};
walk(document);
for (const t of texts) {
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    if (!(tag === 'button' || tag === 'a' || tag === 'input' || el.getAttribute('role') === 'button')) continue;
    const label = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
    if (label && label.indexOf(t) !== -1 && visible(el)) { el.click(); return t; }
  }
}
return null;
"""


def _accept_in_shadow_dom(driver) -> bool:
    """Usercentrics y similares viven en un shadow root, invisible para XPath."""
    texts = [t.lower() for t in COOKIE_TEXTS_CONTAINS]
    try:
        matched = driver.execute_script(_SHADOW_CLICK_JS, texts)
    except Exception as exc:
        logger.debug("No se pudo recorrer el shadow DOM: %s", exc)
        return False
    if matched:
        logger.info("Aceptadas cookies en shadow DOM con el texto %r", matched)
        time.sleep(1.5)
        return True
    return False


_BANNER_PRESENT_JS = r"""
const selectors = arguments[0];
for (const sel of selectors) {
  let els;
  try { els = document.querySelectorAll(sel); } catch (e) { continue; }
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 100 || r.height < 40) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    const text = (el.innerText || '').toLowerCase();
    if (text.indexOf('cookie') !== -1 || text.indexOf('consent') !== -1 || text.indexOf('consentimiento') !== -1) {
      return sel;
    }
  }
}
return null;
"""


def _consent_banner_present(driver) -> Optional[str]:
    try:
        return driver.execute_script(_BANNER_PRESENT_JS, COOKIE_BANNER_SELECTORS)
    except Exception:
        return None


def save_debug_snapshot(driver, name: str) -> None:
    """Guarda captura + HTML para poder ver qué aviso quedó sin aceptar."""
    try:
        os.makedirs(DEBUG_FOLDER, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        png_path = os.path.join(DEBUG_FOLDER, "%s-%s.png" % (name, stamp))
        html_path = os.path.join(DEBUG_FOLDER, "%s-%s.html" % (name, stamp))
        driver.save_screenshot(png_path)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.info("Guardado diagnóstico en %s y %s", png_path, html_path)
    except Exception as exc:
        logger.debug("No se pudo guardar el diagnóstico %s: %s", name, exc)


_BRUTE_ACCEPT_JS = r"""
const phrases = arguments[0];
const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
const nodes = Array.from(document.querySelectorAll('*')).reverse();  // más profundos primero
for (const phrase of phrases) {
  for (const el of nodes) {
    if (el.children.length > 3) continue;
    const text = norm(el.innerText || el.textContent);
    if (!text || text.length > 40) continue;
    if (text.indexOf(phrase) === -1) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    // Algunos banners registran el handler en un ancestro (React/Vue),
    // así que clicamos también hacia arriba unos niveles.
    let node = el;
    for (let i = 0; i < 4 && node; i++) {
      try { node.click(); } catch (e) {}
      node = node.parentElement;
    }
    return text;
  }
}
return null;
"""


def _brute_force_accept(driver) -> bool:
    """Último recurso: clicar por JS el nodo visible cuyo texto sea 'aceptar todo'.

    Cubre banners cuyo botón no es button/a/[role=button] y cuyo manejador de
    click cuelga de un ancestro, que es lo que no alcanzan los XPaths.
    """
    phrases = [t.lower() for t in COOKIE_TEXTS_CONTAINS] + ["aceptar", "accept"]
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        matched = driver.execute_script(_BRUTE_ACCEPT_JS, phrases)
    except Exception as exc:
        logger.debug("Falló el intento por fuerza bruta: %s", exc)
        return False
    if not matched:
        return False
    time.sleep(2)
    if _consent_banner_present(driver):
        logger.debug("Clicado %r por fuerza bruta pero el aviso sigue visible.", matched)
        return False
    logger.info("Aceptadas cookies por fuerza bruta sobre el texto %r", matched)
    return True


def accept_cookies(driver, timeout: int = 20) -> bool:
    """Acepta el aviso de cookies. Reintenta porque el banner se inyecta por JS.

    Devuelve True si se aceptó (o si no había nada que aceptar) y False si
    quedó un aviso visible sin poder cerrarlo.
    """
    deadline = time.time() + timeout
    banner_seen = False

    while time.time() < deadline:
        banner = _consent_banner_present(driver)
        banner_seen = banner_seen or bool(banner)

        if _accept_in_current_context(driver, "documento principal"):
            return True
        if _accept_in_shadow_dom(driver):
            return True
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if _accept_in_iframes(driver):
            return True

        if banner_seen and not _consent_banner_present(driver):
            logger.info("El aviso de cookies ya no está visible.")
            return True

        time.sleep(1)

    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    if not banner_seen:
        logger.info("No se detectó ningún aviso de cookies; continúo.")
        return True

    logger.info("Los métodos normales no aceptaron el aviso; pruebo por fuerza bruta.")
    if _brute_force_accept(driver):
        return True

    logger.warning("Hay un aviso de cookies visible que no se pudo aceptar.")
    save_debug_snapshot(driver, "cookies")
    return False


def find_element_by_text(driver, text_values: list):
    """Primer elemento clicable y visible cuyo texto contenga alguno de los valores.

    Usa los mismos XPaths acotados que el aviso de cookies: nunca devuelve un
    contenedor envolvente que se limita a *contener* el texto.
    """
    for text in text_values:
        for xpath in _text_xpaths(text.lower(), exact=False):
            try:
                elements = driver.find_elements(By.XPATH, xpath)
            except Exception:
                continue
            for element in elements:
                if _is_interactable(element):
                    return element, text
    return None, None


def find_and_click(driver, text_values: list, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while True:
        element, text = find_element_by_text(driver, text_values)
        if element is not None and safe_click(driver, element):
            logger.info("Clicado elemento con texto %r", text)
            return True
        if time.time() >= deadline:
            logger.debug("No se encontró ningún elemento clicable de %r", text_values)
            return False
        time.sleep(1)


GOOGLE_BUTTON_TEXTS = [
    "continuar con google",
    "iniciar sesión con google",
    "acceder con google",
    "entrar con google",
    "ingresar con google",
    "continue with google",
    "sign in with google",
    "log in with google",
]


def click_google_button(driver, timeout: int = 12) -> bool:
    """Pulsa el botón de 'Continuar con Google' de Holded.

    El botón puede ser nativo o el widget de Google Identity Services, que se
    renderiza dentro de un iframe de accounts.google.com.
    """
    if find_and_click(driver, GOOGLE_BUTTON_TEXTS, timeout=timeout):
        return True

    frame_selectors = (
        "iframe[src*='accounts.google.com'], iframe[id^='gsi_'], "
        "iframe[title*='Google' i], iframe[src*='gsi/button']"
    )
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, frame_selectors)
    except Exception:
        frames = []
    for index, frame in enumerate(frames):
        try:
            driver.switch_to.frame(frame)
        except Exception:
            continue
        try:
            if find_and_click(driver, GOOGLE_BUTTON_TEXTS + ["google"], timeout=3):
                logger.info("Botón de Google pulsado dentro del iframe #%d", index)
                return True
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    # Último recurso: enlaces cuyo destino ya es el propio flujo OAuth de Google.
    try:
        links = driver.find_elements(
            By.CSS_SELECTOR,
            "a[href*='accounts.google.com'], a[href*='/auth/google'], "
            "button[class*='google' i], div[class*='google' i][role='button']",
        )
    except Exception:
        links = []
    for link in links:
        if _is_interactable(link) and safe_click(driver, link):
            logger.info("Pulsado enlace/botón de Google por selector CSS.")
            return True

    return False


def _switch_to_google_context(driver, original_handle: str, timeout: int = 20) -> bool:
    """Si el SSO abre una ventana emergente, pasa a ella. True si vemos Google."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            handles = driver.window_handles
        except Exception:
            return False
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if "accounts.google.com" in driver.current_url.lower():
                    if handle != original_handle:
                        logger.info("El SSO de Google se abrió en una ventana emergente.")
                    return True
            except Exception:
                continue
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _fill_google_field(driver, element, value: str) -> None:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    except Exception:
        pass
    element.click()
    element.clear()
    element.send_keys(value)


def _google_next(driver, fallback_element) -> None:
    """Pulsa 'Siguiente'; si no aparece el botón, envía ENTER en el campo."""
    for selector in ("#identifierNext button", "#passwordNext button", "#identifierNext", "#passwordNext"):
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for button in buttons:
            if _is_interactable(button) and safe_click(driver, button):
                return
    if find_and_click(driver, ["siguiente", "next"], timeout=3):
        return
    fallback_element.send_keys(Keys.ENTER)


def _visible_element(driver, selector: str):
    try:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if _is_interactable(element):
                return element
    except Exception:
        pass
    return None


def complete_google_sso(driver, email: str, password: str, timeout: int = 150) -> bool:
    """Recorre las pantallas de accounts.google.com hasta volver a Holded.

    Es una máquina de estados en bucle porque Google encadena pantallas
    distintas según la sesión previa: selector de cuenta, correo, contraseña y,
    a veces, una pantalla de consentimiento.
    """
    deadline = time.time() + timeout
    email_done = False
    password_done = False

    while time.time() < deadline:
        try:
            url = driver.current_url.lower()
        except Exception:
            time.sleep(1)
            continue

        if "app.holded.com" in url and "login" not in url:
            # Al volver del callback de OAuth se pasa un instante por una URL
            # de Holded aunque la sesión no haya cuajado: un momento después
            # rebota a /login. Confirmamos que la URL se sostiene antes de dar
            # el login por bueno.
            time.sleep(5)
            try:
                settled = driver.current_url.lower()
            except Exception:
                settled = url
            if "login" not in settled:
                logger.info("Login por Google completado; de vuelta en Holded.")
                return True
            logger.info("Holded rebotó al login tras el callback; sigo con el SSO.")
            continue

        if "accounts.google.com" not in url:
            time.sleep(1)
            continue

        # Pantalla de "elige una cuenta": pulsamos directamente la del correo.
        if not email_done:
            account_xpath = (
                "//*[@data-identifier='%s']"
                " | //*[contains(normalize-space(.), '%s')][not(*)]" % (email, email)
            )
            try:
                tiles = driver.find_elements(By.XPATH, account_xpath)
            except Exception:
                tiles = []
            for tile in tiles:
                if _is_interactable(tile) and safe_click(driver, tile):
                    logger.info("Seleccionada la cuenta %s en el selector de Google.", email)
                    email_done = True
                    time.sleep(3)
                    break
            if email_done:
                continue

        email_input = _visible_element(driver, "input[type='email'], input#identifierId, input[name='identifier']")
        if email_input is not None and not email_done:
            logger.info("Introduciendo el correo en Google...")
            _fill_google_field(driver, email_input, email)
            _google_next(driver, email_input)
            email_done = True
            time.sleep(3)
            continue

        password_input = _visible_element(driver, "input[type='password'], input[name='Passwd'], input[name='password']")
        if password_input is not None and not password_done:
            logger.info("Introduciendo la contraseña en Google...")
            _fill_google_field(driver, password_input, password)
            _google_next(driver, password_input)
            password_done = True
            time.sleep(5)
            continue

        # Selector de cuenta sin la nuestra: pedimos entrar con otra.
        if not email_done and find_and_click(
            driver, ["usar otra cuenta", "use another account", "añadir otra cuenta"], timeout=2
        ):
            logger.info("La cuenta no estaba en el selector; pido 'usar otra cuenta'.")
            time.sleep(2)
            continue

        # Pantalla de consentimiento / "¿Continuar con Holded?".
        if find_and_click(driver, ["continuar", "continue", "permitir", "allow", "aceptar todo"], timeout=2):
            logger.info("Aceptada la pantalla de consentimiento de Google.")
            time.sleep(3)
            continue

        if any(token in url for token in ("challenge", "signin/rejected", "deniedsigninrejected", "speedbump")):
            logger.warning(
                "Google ha pedido una verificación adicional (2FA o bloqueo por navegador "
                "automatizado). Complétala manualmente por VNC: la sesión queda guardada "
                "en el perfil de Chrome y las siguientes ejecuciones no la pedirán."
            )
            save_debug_snapshot(driver, "google-challenge")
            return False

        time.sleep(1)

    logger.warning("Se agotó el tiempo esperando a que Google devolviera el control a Holded.")
    save_debug_snapshot(driver, "google-timeout")
    return False


def google_login(driver, email: str, password: str) -> bool:
    try:
        original_handle = driver.current_window_handle
    except Exception:
        original_handle = None

    if not click_google_button(driver):
        logger.info("No se encontró el botón de 'Continuar con Google'.")
        return False

    logger.info("Pulsado 'Continuar con Google'; esperando a accounts.google.com...")
    time.sleep(3)

    if original_handle and not _switch_to_google_context(driver, original_handle):
        # Puede que Google reutilizara la sesión y ya estemos dentro de Holded.
        if "app.holded.com" in driver.current_url.lower() and "login" not in driver.current_url.lower():
            logger.info("Google reutilizó la sesión existente; ya estamos dentro de Holded.")
            return True
        logger.warning("Tras pulsar el botón de Google no se abrió accounts.google.com.")
        save_debug_snapshot(driver, "google-no-redirect")
        return False

    accept_cookies(driver, timeout=8)
    ok = complete_google_sso(driver, email, password)

    # Si el SSO usó una ventana emergente, esta se cierra al terminar.
    if original_handle:
        try:
            if original_handle in driver.window_handles:
                driver.switch_to.window(original_handle)
        except Exception:
            pass
    return ok


def is_already_logged_in(driver) -> bool:
    driver.get(HOLDEN_INVOICES_URL)
    time.sleep(5)
    current_url = driver.current_url.lower()
    if "login" in current_url or "signin" in current_url or "accounts.google.com" in current_url:
        return False
    return True


def login(
    driver,
    email: str,
    password: str,
    otp: Optional[str] = None,
    google_email: Optional[str] = None,
    google_password: Optional[str] = None,
) -> None:
    logger.info("Entrando en Holded...")
    driver.get(HOLDEN_LOGIN_URL)
    wait_for_page_ready(driver, timeout=30)
    accept_cookies(driver)

    if google_email and google_password:
        if google_login(driver, google_email, google_password):
            # No nos fiamos de que el SSO diga que terminó: comprobamos que la
            # sesión aguanta una navegación real antes de seguir.
            if is_already_logged_in(driver):
                logger.info("Sesión de Holded confirmada tras el SSO de Google.")
                return
            logger.warning("El SSO dijo haber terminado pero Holded sigue pidiendo login.")
            save_debug_snapshot(driver, "sso-sin-sesion")
        logger.warning("El login por Google no prosperó; intento el formulario de Holded.")
        driver.get(HOLDEN_LOGIN_URL)
        wait_for_page_ready(driver, timeout=30)
        accept_cookies(driver, timeout=8)
    else:
        logger.info("Sin credenciales de Google (GOOGLE_EMAIL/GOOGLE_PASSWORD); uso el formulario de Holded.")

    if not email or not password:
        raise RuntimeError(
            "No se pudo entrar por Google y no hay HOLDED_EMAIL/HOLDED_PASSWORD para el formulario."
        )

    # Sólo buscamos un botón que despliegue el formulario si este no está ya a
    # la vista: en la pantalla actual de Holded los campos vienen visibles, y
    # pulsar "Iniciar sesión" antes de rellenarlos únicamente dispara la
    # validación de "Este campo es obligatorio".
    if _visible_element(driver, "input[type='email'], input[name*='email'], input[id*='email']") is None:
        if find_and_click(driver, ["iniciar sesión", "login", "sign in", "entrar"]):
            time.sleep(2)

    email_input = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='email' or contains(@name, 'email') or contains(@id, 'email') or contains(@placeholder, 'email')]"))
    )
    email_input.clear()
    email_input.send_keys(email)
    email_input.send_keys(Keys.ENTER)

    password_input = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='password' or contains(@name, 'password')]"))
    )
    driver.execute_script("arguments[0].scrollIntoView(true);", password_input)
    password_input.clear()
    password_input.send_keys(password)
    password_input.send_keys(Keys.ENTER)

    time.sleep(5)
    if otp:
        try:
            otp_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//input[contains(@name, 'otp') or contains(@name, 'code') or @type='tel']",
                    )
                )
            )
            otp_input.clear()
            otp_input.send_keys(otp)
            otp_input.send_keys(Keys.ENTER)
            time.sleep(5)
        except TimeoutException:
            logger.info("No se solicitó OTP o no se encontró el campo OTP.")

    WebDriverWait(driver, 30).until(lambda d: "login" not in d.current_url.lower())
    logger.info("Login completado con éxito.")


def subscription_panel_open(driver) -> bool:
    """True si el drawer de 'Facturas de tu plan Holded' está montado."""
    try:
        return bool(
            driver.find_elements(
                By.XPATH,
                "//*[contains(%s, '%s')]" % (_xpath_lower("."), SUBSCRIPTION_PANEL_TEXT),
            )
        )
    except Exception:
        return False


def wait_for_subscription_panel(driver, timeout: int = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if subscription_panel_open(driver):
            logger.info("Panel de facturas de suscripción abierto.")
            return True
        time.sleep(1)
    return False


def open_subscription_drawer(driver, timeout: int = 30) -> bool:
    """Fuerza el fragmento sobre la página ya cargada.

    Sólo como plan B: con la sesión iniciada, la URL completa abre el panel
    por sí sola.
    """
    try:
        driver.execute_script(
            "if (window.location.hash === '#' + arguments[0]) { window.location.hash = ''; }"
            "window.location.hash = arguments[0];"
            "window.dispatchEvent(new HashChangeEvent('hashchange', {newURL: window.location.href}));",
            SUBSCRIPTION_HASH,
        )
    except Exception as exc:
        logger.warning("No se pudo forzar el fragmento de suscripción: %s", exc)
        return False
    return wait_for_subscription_panel(driver, timeout)


def navigate_to_invoices(driver) -> None:
    logger.info("Navegando a la página de facturas de Holded...")
    driver.get(HOLDEN_INVOICES_URL)
    wait_for_page_ready(driver, timeout=30)
    accept_cookies(driver)

    if wait_for_subscription_panel(driver):
        return

    # Plan B: cargar /home limpio y forzar el fragmento después.
    logger.warning("El panel no apareció con la URL directa; fuerzo el fragmento sobre /home.")
    driver.get(HOLDEN_HOME_URL)
    wait_for_page_ready(driver, timeout=30)
    time.sleep(3)
    if open_subscription_drawer(driver):
        return

    logger.warning("No se pudo abrir el panel de facturas de suscripción.")
    save_debug_snapshot(driver, "sin-panel-suscripcion")


def _wait_for_new_window(driver, known_handles: set, timeout: int = 30) -> Optional[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for handle in driver.window_handles:
                if handle not in known_handles:
                    return handle
        except Exception:
            pass
        time.sleep(0.5)
    return None


def open_latest_invoice(driver) -> Optional[tuple]:
    """Pulsa el estado 'Pagada' de la factura más reciente.

    Holded abre el PDF en una ventana nueva. Nos quedamos con su URL y su
    título (que es el número de factura) y la cerramos: el PDF se descarga
    aparte, no desde el visor.
    """
    original = driver.current_window_handle
    known = set(driver.window_handles)

    if not find_and_click(driver, ["pagada", "paid"]):
        logger.warning("No se encontró ninguna factura con estado 'Pagada'.")
        save_debug_snapshot(driver, "sin-facturas")
        return None

    handle = _wait_for_new_window(driver, known)
    if handle is None:
        logger.warning("Al pulsar la factura no se abrió la ventana del PDF.")
        save_debug_snapshot(driver, "sin-ventana-pdf")
        return None

    driver.switch_to.window(handle)
    try:
        url = driver.current_url
        title = (driver.title or "").strip()
    finally:
        try:
            driver.close()
        except Exception:
            pass
        driver.switch_to.window(original)

    logger.info("Factura más reciente: %s (%s)", title or "sin título", url)
    return url, title


def _invoice_filename(url: str, title: str) -> str:
    """Nombre de fichero a partir del número de factura (título de la ventana)."""
    number = re.sub(r"[^A-Za-z0-9_-]", "", title.split("-")[0].strip())
    if not number:
        number = re.sub(r"[^A-Za-z0-9_-]", "", url.rstrip("/").split("/")[-1])[:32]
    return "%s.pdf" % (number or "factura-holded")


def download_pdf_with_session(driver, url: str, filename: str) -> Optional[str]:
    """Descarga el PDF reutilizando las cookies de la sesión del navegador.

    Es más fiable que dejar que Chrome lo descargue: su visor integrado abre
    los PDF en lugar de guardarlos, y las `prefs` de descarga no se aplican
    cuando el driver se conecta a un Chrome ya lanzado en vez de arrancarlo.
    """
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain"), path=cookie.get("path", "/")
        )

    headers = {}
    try:
        headers["User-Agent"] = driver.execute_script("return navigator.userAgent;")
    except Exception:
        pass

    response = session.get(url, headers=headers, timeout=90)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
        logger.warning(
            "La respuesta de %s no parece un PDF (Content-Type: %s). No la guardo.", url, content_type or "desconocido"
        )
        return None

    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    path = os.path.join(DOWNLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(response.content)
    logger.info("Factura guardada en %s (%d KB)", path, len(response.content) // 1024)
    return path


def download_invoice_from_holded(driver) -> bool:
    navigate_to_invoices(driver)

    opened = open_latest_invoice(driver)
    if opened is None:
        return False

    url, title = opened
    try:
        return download_pdf_with_session(driver, url, _invoice_filename(url, title)) is not None
    except Exception as exc:
        logger.warning("No se pudo descargar el PDF de %s: %s", url, exc)
        return False


def load_last_run_date() -> Optional[date]:
    if not os.path.exists(LAST_RUN_FILE):
        return None
    try:
        with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
            return datetime.strptime(f.read().strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def mark_last_run(date_value: datetime.date) -> None:
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        f.write(date_value.strftime("%Y-%m-%d"))


def release_shared_driver(driver) -> None:
    """Suelta el driver sin cerrar el Chrome que lanzó start.sh.

    `driver.quit()` cerraría ese navegador, y con él la sesión de Google que
    vive en el perfil: el intento del mes siguiente tendría que volver a
    loguearse desde cero y depender de que Google no pida verificación.
    Dejamos abierta sólo la pestaña original en about:blank.
    """
    try:
        handles = driver.window_handles
        for handle in handles[1:]:
            driver.switch_to.window(handle)
            driver.close()
        if handles:
            driver.switch_to.window(handles[0])
            driver.get("about:blank")
        logger.info("Chrome compartido liberado sin cerrarlo; la sesión queda viva.")
    except Exception as exc:
        logger.warning("No se pudo dejar limpio el Chrome compartido: %s", exc)

    # undetected_chromedriver llama a quit() al destruir el objeto, lo que
    # mataría el navegador más tarde: lo anulamos en esta instancia.
    try:
        driver.quit = lambda *args, **kwargs: None
    except Exception:
        pass


def download_invoice() -> None:
    email = get_env("HOLDED_EMAIL")
    password = get_env("HOLDED_PASSWORD")
    otp = get_env("HOLDED_OTP")
    google_email = get_env("GOOGLE_EMAIL")
    google_password = get_env("GOOGLE_PASSWORD")
    headless = get_env("HOLDED_HEADLESS", "false").lower() in ("1", "true", "yes")

    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    user_data_dir = USER_DATA_DIR
    os.makedirs(user_data_dir, exist_ok=True)

    shared_chrome = not headless
    try:
        driver = build_driver(
            DOWNLOAD_FOLDER,
            user_data_dir=user_data_dir,
            headless=headless,
            use_existing_chrome=not headless,
            debugger_address="127.0.0.1:9223",
        )
    except Exception as exc:
        logger.warning("No se pudo conectar a Chrome existente (%s). Iniciando sesión nueva...", exc)
        shared_chrome = False
        driver = build_driver(
            DOWNLOAD_FOLDER,
            user_data_dir=user_data_dir,
            headless=headless,
            use_existing_chrome=False,
        )
    set_download_folder(driver, DOWNLOAD_FOLDER)
    try:
        if not is_already_logged_in(driver):
            logger.info("No hay sesión activa. Iniciando login de Holded...")
            if not (google_email and google_password) and not (email and password):
                raise RuntimeError(
                    "No hay sesión activa y faltan credenciales "
                    "(GOOGLE_EMAIL/GOOGLE_PASSWORD o HOLDED_EMAIL/HOLDED_PASSWORD)."
                )
            login(driver, email, password, otp, google_email, google_password)
        else:
            logger.info("Sesión Holded ya activa, saltando login.")

        if download_invoice_from_holded(driver):
            logger.info("Factura descargada correctamente en %s", DOWNLOAD_FOLDER)
            mark_last_run(datetime.now().date())
        else:
            raise RuntimeError("No se pudo descargar la factura del mes.")
        time.sleep(10)
    except Exception:
        # Dejar rastro antes de cerrar: si no, el navegador desaparece y no se
        # puede ver en qué pantalla se quedó.
        save_debug_snapshot(driver, "error")
        keep_open = int(get_env("HOLDED_KEEP_OPEN_SECONDS", "0") or 0)
        if keep_open > 0:
            logger.warning(
                "Fallo en la ejecución. Dejo el navegador abierto %d s para inspeccionarlo por VNC.",
                keep_open,
            )
            time.sleep(keep_open)
        raise
    finally:
        if shared_chrome:
            release_shared_driver(driver)
        else:
            driver.quit()


def next_monthly_run() -> datetime:
    now = datetime.now()
    year = now.year
    month = now.month
    while True:
        days_in_month = calendar.monthrange(year, month)[1]
        if days_in_month >= 7:
            candidate = datetime(year, month, 7, 22, 50)
            if candidate > now:
                return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1


def should_run_today() -> bool:
    now = datetime.now()
    if now.day != 7:
        return False
    if now.hour < 22 or (now.hour == 22 and now.minute < 50):
        return False
    last_run = load_last_run_date()
    return last_run != now.date()


def run_scheduler() -> None:
    logger.info("Iniciando programador mensual de Holded.")
    next_run = next_monthly_run()
    logger.info("Hora programada de la próxima ejecución: %s", next_run.strftime("%Y-%m-%d %H:%M"))
    if should_run_today():
        try:
            logger.info("Hoy toca ejecución y aún no se ha lanzado. Ejecutando ahora.")
            download_invoice()
        except Exception as exc:
            logger.exception("Error en la ejecución de recuperación inmediata: %s", exc)

    while True:
        target = next_monthly_run()
        logger.info("Siguiente ejecución programada: %s", target.strftime("%Y-%m-%d %H:%M"))

        wait_seconds = (target - datetime.now()).total_seconds()
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        try:
            logger.info("Ejecutando descarga de factura de Holded.")
            download_invoice()
        except Exception as exc:
            logger.exception("Error al descargar la factura: %s", exc)

        time.sleep(60)


if __name__ == "__main__":
    run_scheduler()
