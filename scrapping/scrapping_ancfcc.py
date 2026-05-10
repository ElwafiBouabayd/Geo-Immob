import time
import re
import os
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager


URL = "https://www.ancfcc.gov.ma/ValeursVenalesPage/"

CASA_CONSERVATIONS = [
    "CASA AIN CHOCK",
    "CASA AIN SBAA HAY MOHAMMADI",
    "CASA ANFA",
    "CASA ELFIDA MERS SULTAN",
    "CASA HAY HASSANI",
    "CASA MAARIF",
    "CASA NOUACER",
    "CASA SIDI EL BERNOUSSI- ZNATA",
    "CASA SIDI OTHMANE",
    "DAR BOUAZZA",
    "MEDYOUNA",
]

OUTPUT_DIR = "data/ancfcc_casa_csv"


def normalize_text(text):
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def slugify_filename(text):
    text = normalize_text(text).lower()
    text = text.replace("/", "-")
    text = text.replace("\\", "-")
    text = text.replace(":", "-")
    text = text.replace("*", "")
    text = text.replace("?", "")
    text = text.replace('"', "")
    text = text.replace("<", "")
    text = text.replace(">", "")
    text = text.replace("|", "")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_\-]", "", text)
    return text


def sleep(sec=2):
    time.sleep(sec)


def setup_driver(headless=False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=fr-FR")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def select_mode_recherche_normale(driver):
    selects = driver.find_elements(By.TAG_NAME, "select")

    mode_select = None
    for sel in selects:
        try:
            if not sel.is_displayed():
                continue
            options = [normalize_text(o.text) for o in sel.find_elements(By.TAG_NAME, "option")]
            if "Recherche normale" in options:
                mode_select = sel
                break
        except Exception:
            continue

    if mode_select is None:
        raise RuntimeError("Dropdown 'Mode de recherche' introuvable.")

    sel = Select(mode_select)
    found = False
    for opt in sel.options:
        if normalize_text(opt.text) == "Recherche normale":
            sel.select_by_visible_text(opt.text)
            found = True
            break

    if not found:
        raise RuntimeError("Option 'Recherche normale' introuvable.")

    driver.execute_script(
        "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
        mode_select
    )
    sleep(2)


def find_conservation_select(driver):
    selects = driver.find_elements(By.TAG_NAME, "select")

    target_markers = {"CASA ANFA", "CASA MAARIF", "DAR BOUAZZA", "MEDYOUNA"}
    best_select = None
    best_score = -1

    for sel in selects:
        try:
            if not sel.is_displayed():
                continue
            options = sel.find_elements(By.TAG_NAME, "option")
            texts = {normalize_text(o.text) for o in options}
            score = sum(1 for marker in target_markers if marker in texts)

            if score > best_score:
                best_score = score
                best_select = sel
        except StaleElementReferenceException:
            continue

    if best_select is None or best_score <= 0:
        raise RuntimeError("Dropdown 'Conservation foncière' introuvable.")

    return best_select


def select_option_js(driver, select_element, target_text):
    sel = Select(select_element)
    target_norm = normalize_text(target_text)

    chosen_value = None
    available = []

    for option in sel.options:
        txt = normalize_text(option.text)
        val = normalize_text(option.get_attribute("value"))
        available.append(txt)
        if txt == target_norm or val == target_norm:
            chosen_value = option.get_attribute("value")
            break

    if chosen_value is None:
        raise RuntimeError(
            f"Option non trouvée: {target_text}\nOptions disponibles: {available}"
        )

    driver.execute_script("""
        const select = arguments[0];
        const value = arguments[1];
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    """, select_element, chosen_value)

    sleep(2)


def find_consulter_button(driver):
    candidates = driver.find_elements(
        By.XPATH,
        "//button[contains(normalize-space(), 'Consulter')] | "
        "//input[@type='submit' and contains(@value, 'Consulter')]"
    )

    visible_candidates = []
    for btn in candidates:
        try:
            if btn.is_displayed():
                visible_candidates.append(btn)
        except Exception:
            pass

    if not visible_candidates:
        raise RuntimeError("Bouton 'Consulter' introuvable.")

    btn = visible_candidates[0]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
    sleep(1)

    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)

    sleep(4)


def find_result_table(driver):
    tables = driver.find_elements(By.TAG_NAME, "table")

    for table in tables:
        try:
            rows = table.find_elements(By.XPATH, ".//tr")
            if len(rows) < 2:
                continue

            header_cells = table.find_elements(By.XPATH, ".//tr[1]//th | .//tr[1]//td")
            headers = [normalize_text(c.text) for c in header_cells]
            joined = " | ".join(headers).lower()

            if "zone" in joined and ("valeur" in joined or "dh/m²" in joined or "dh/m2" in joined):
                return table
        except Exception:
            continue

    raise RuntimeError("Tableau résultat introuvable.")


def extract_rows_from_current_page(table):
    header_cells = table.find_elements(By.XPATH, ".//tr[1]//th | .//tr[1]//td")
    headers = [normalize_text(c.text) for c in header_cells]

    data = []
    rows = table.find_elements(By.XPATH, ".//tr[position()>1]")

    for tr in rows:
        cells = tr.find_elements(By.TAG_NAME, "td")
        vals = [normalize_text(td.text) for td in cells]
        if any(vals):
            data.append(vals)

    return headers, data


def align_rows(headers, rows):
    n = len(headers)
    fixed = []
    for row in rows:
        if len(row) < n:
            row = row + [""] * (n - len(row))
        elif len(row) > n:
            row = row[:n]
        fixed.append(row)
    return fixed


def get_current_page_number(driver):
    candidates = driver.find_elements(
        By.XPATH,
        "//*[contains(@class, 'active') or contains(@class, 'current') or self::span or self::a or self::button]"
    )
    for c in candidates:
        try:
            txt = normalize_text(c.text)
            if txt.isdigit():
                cls = (c.get_attribute("class") or "").lower()
                aria = (c.get_attribute("aria-current") or "").lower()
                if "active" in cls or "current" in cls or aria:
                    return int(txt)
        except Exception:
            pass
    return None


def click_next_page(driver, previous_first_row=None):
    next_candidates = driver.find_elements(
        By.XPATH,
        "//a[contains(normalize-space(), 'Suivant')] | "
        "//button[contains(normalize-space(), 'Suivant')] | "
        "//*[self::a or self::button or self::span][contains(normalize-space(), 'Suivant')]"
    )

    usable = []
    for el in next_candidates:
        try:
            if not el.is_displayed():
                continue
            cls = (el.get_attribute('class') or '').lower()
            aria_disabled = (el.get_attribute('aria-disabled') or '').lower()
            if "disabled" in cls or aria_disabled == "true":
                continue
            usable.append(el)
        except Exception:
            continue

    if not usable:
        return False

    next_btn = usable[-1]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
    sleep(1)

    try:
        next_btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", next_btn)

    sleep(3)

    if previous_first_row is not None:
        for _ in range(10):
            try:
                table = find_result_table(driver)
                _, rows = extract_rows_from_current_page(table)
                current_first = tuple(rows[0]) if rows else None
                if current_first != previous_first_row:
                    return True
            except Exception:
                pass
            sleep(1)

    return True


def extract_all_pages(driver, conservation_name):
    all_rows = []
    headers_master = None
    seen_pages = set()
    max_pages_guard = 500

    for _ in range(max_pages_guard):
        table = find_result_table(driver)
        headers, rows = extract_rows_from_current_page(table)

        if not rows:
            break

        rows = align_rows(headers, rows)

        if headers_master is None:
            headers_master = headers

        current_page = get_current_page_number(driver)
        if current_page is not None:
            if current_page in seen_pages:
                break
            seen_pages.add(current_page)

        all_rows.extend(rows)

        first_row_signature = tuple(rows[0]) if rows else None
        moved = click_next_page(driver, previous_first_row=first_row_signature)
        if not moved:
            break

    if headers_master is None:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=headers_master)
    df["Conservation_scrapee"] = conservation_name
    return df.drop_duplicates()


def save_conservation_csv(df, conservation_name):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = slugify_filename(conservation_name) + ".csv"
    filepath = os.path.join(OUTPUT_DIR, filename)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    print(f"[SAVE] {filepath}")


def scrape_one_conservation(driver, wait, conservation_name):
    print(f"\n[INFO] Scraping: {conservation_name}")

    driver.get(URL)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    sleep(3)

    select_mode_recherche_normale(driver)
    select_cf = find_conservation_select(driver)
    select_option_js(driver, select_cf, conservation_name)
    find_consulter_button(driver)

    df = extract_all_pages(driver, conservation_name)

    if df.empty:
        print(f"[WARN] Aucun résultat détecté pour {conservation_name}")
    else:
        print(f"[OK] {conservation_name}: {len(df)} lignes extraites")

    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    driver = setup_driver(headless=False)
    wait = WebDriverWait(driver, 20)
    all_dfs = []

    try:
        for conservation in CASA_CONSERVATIONS:
            try:
                df = scrape_one_conservation(driver, wait, conservation)

                if not df.empty:
                    df.columns = [normalize_text(c) for c in df.columns]

                    # un CSV par conservation
                    save_conservation_csv(df, conservation)

                    all_dfs.append(df)

                    # sauvegarde globale temporaire
                    temp_df = pd.concat(all_dfs, ignore_index=True).drop_duplicates()
                    temp_df.to_csv(
                        os.path.join(OUTPUT_DIR, "valeurs_venales_casa_global_temp.csv"),
                        index=False,
                        encoding="utf-8-sig"
                    )

            except Exception as e:
                print(f"[ERROR] {conservation}: {e}")

        if not all_dfs:
            print("\nAucune donnée extraite.")
            return

        final_df = pd.concat(all_dfs, ignore_index=True).drop_duplicates()
        final_df.columns = [normalize_text(c) for c in final_df.columns]

        final_df.to_csv(
            os.path.join(OUTPUT_DIR, "valeurs_venales_casa_global.csv"),
            index=False,
            encoding="utf-8-sig"
        )
        final_df.to_excel(
            os.path.join(OUTPUT_DIR, "valeurs_venales_casa_global.xlsx"),
            index=False
        )

        print("\nExtraction terminée.")
        print(f"Lignes extraites : {len(final_df)}")
        print(f"Dossier de sortie : {OUTPUT_DIR}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()