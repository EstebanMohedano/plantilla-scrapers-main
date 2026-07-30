import calendar
import logging
import os
import shutil
import time
from datetime import date, datetime
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HOLDEN_LOGIN_URL = "https://app.holded.com/login"
HOLDEN_INVOICES_URL = "https://app.holded.com/sales/revenue#settings:/subscription/invoices"
DOWNLOAD_FOLDER = "/app/data/holded_downloads"
USER_DATA_DIR = "/app/data/holded_user_data"
LAST_RUN_FILE = "/app/data/holded_invoice_last_run.txt"


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


def accept_cookies(driver, timeout: int = 10) -> bool:
    cookie_texts = [
        "aceptar cookies",
        "aceptar todo",
        "permitir todo",
        "aceptar",
        "accept cookies",
        "accept all",
        "allow cookies",
        "allow all",
        "ok",
        "cerrar",
    ]
    for text in cookie_texts:
        xpath = (
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '" + text.lower() + "')]"
            " | //a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '" + text.lower() + "')]"
            " | //span[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '" + text.lower() + "')]"
            " | //div[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '" + text.lower() + "')]"
        )
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            if safe_click(driver, element):
                logger.info("Aceptadas cookies / cerrado modal con texto %r", text)
                time.sleep(2)
                return True
        except TimeoutException:
            continue
    return False


def find_and_click(driver, text_values: list, timeout: int = 20) -> bool:
    for text in text_values:
        xpath = (
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '" \
            + text.lower()
            + "')]"
            " | //a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '"
            + text.lower()
            + "')]"
            " | //span[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '"
            + text.lower()
            + "')]"
            " | //div[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '"
            + text.lower()
            + "')]")
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            element.click()
            logger.info("Clicado elemento con texto %r", text)
            return True
        except TimeoutException:
            logger.debug("No se encontró elemento con texto %r", text)
    return False


def is_already_logged_in(driver) -> bool:
    driver.get(HOLDEN_INVOICES_URL)
    time.sleep(5)
    current_url = driver.current_url.lower()
    if "login" in current_url or "signin" in current_url or "accounts.google.com" in current_url:
        return False
    return True


def login(driver, email: str, password: str, otp: Optional[str] = None) -> None:
    logger.info("Entrando en Holded...")
    driver.get(HOLDEN_LOGIN_URL)
    time.sleep(3)
    accept_cookies(driver)

    if find_and_click(driver, ["continuar con google", "iniciar sesión con google", "sign in with google", "continue with google", "google"]):
        logger.info("Intentando login por Google SSO...")
        time.sleep(8)
        WebDriverWait(driver, 30).until(lambda d: "login" not in d.current_url.lower())
        logger.info("Login por Google completado.")
        return

    email_input = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='email' or contains(@name, 'email')]"))
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


def download_invoice_from_holded(driver) -> bool:
    logger.info("Navegando a la página de facturas... %s", HOLDEN_INVOICES_URL)
    driver.get(HOLDEN_INVOICES_URL)
    time.sleep(5)

    if not find_and_click(driver, ["pagada", "paid"]):
        logger.warning("No se encontró un botón 'Pagada'. Continúo de todas formas.")
        time.sleep(3)

    if find_and_click(driver, ["descargar", "download", "ver factura", "view invoice", "factura"]):
        logger.info("Se ha intentado iniciar la descarga.")
        return True

    logger.info("Buscando primera factura para abrirla...")
    invoice_link = None
    candidates = driver.find_elements(
        By.XPATH,
        "//a[contains(@href,'invoice') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'factura') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'invoice')]",
    )
    if candidates:
        invoice_link = candidates[0]
    else:
        rows = driver.find_elements(By.XPATH, "//tr")
        if rows:
            invoice_link = rows[0]

    if invoice_link is None:
        logger.warning("No se detectó ninguna factura para abrir.")
        return False

    try:
        invoice_link.click()
        time.sleep(5)
    except Exception as exc:
        logger.warning("No se pudo abrir la factura automáticamente: %s", exc)

    if find_and_click(driver, ["descargar", "download"]):
        logger.info("Descarga iniciada tras abrir factura.")
        return True

    logger.warning("No se encontró un botón de descarga después de abrir la factura.")
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


def download_invoice() -> None:
    email = get_env("HOLDED_EMAIL")
    password = get_env("HOLDED_PASSWORD")
    otp = get_env("HOLDED_OTP")
    headless = get_env("HOLDED_HEADLESS", "false").lower() in ("1", "true", "yes")

    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    user_data_dir = USER_DATA_DIR
    os.makedirs(user_data_dir, exist_ok=True)

    driver = build_driver(
        DOWNLOAD_FOLDER,
        user_data_dir=user_data_dir,
        headless=headless,
        use_existing_chrome=not headless,
        debugger_address="127.0.0.1:9223",
    )
    set_download_folder(driver, DOWNLOAD_FOLDER)
    try:
        if not is_already_logged_in(driver):
            logger.info("No hay sesión activa. Iniciando login de Holded...")
            if not email or not password:
                raise RuntimeError("No hay sesión activa y faltan HOLDED_EMAIL/HOLDED_PASSWORD.")
            login(driver, email, password, otp)
        else:
            logger.info("Sesión Holded ya activa, saltando login.")

        if download_invoice_from_holded(driver):
            logger.info("Factura descargada correctamente en %s", DOWNLOAD_FOLDER)
            mark_last_run(datetime.now().date())
        else:
            raise RuntimeError("No se pudo descargar la factura del mes.")
        time.sleep(10)
    finally:
        driver.quit()


def next_monthly_run() -> datetime:
    now = datetime.now()
    year = now.year
    month = now.month
    while True:
        days_in_month = calendar.monthrange(year, month)[1]
        if days_in_month >= 30:
            candidate = datetime(year, month, 30, 19, 15)
            if candidate > now:
                return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1


def should_run_today() -> bool:
    now = datetime.now()
    if now.day != 30:
        return False
    if now.hour < 19 or (now.hour == 19 and now.minute < 15):
        return False
    last_run = load_last_run_date()
    return last_run != now.date()


def run_scheduler() -> None:
    logger.info("Iniciando programador mensual de Holded.")
    next_run = next_monthly_run()
    logger.info("Hora programada de la próxima ejecución: %s", next_run.strftime("%Y-%m-%d %H:%M"))
    if should_run_today():
        try:
            logger.info("Hoy es 30 y aún no se ha ejecutado. Ejecutando ahora.")
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
