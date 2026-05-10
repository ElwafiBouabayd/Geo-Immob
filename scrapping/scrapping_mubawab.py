# pip install selenium pandas webdriver-manager beautifulsoup4

import time
import re
import random
import pandas as pd
from pathlib import Path
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


BASE_URL = "https://www.mubawab.ma/fr/ct/casablanca/immobilier-a-vendre"
OUTPUT_FILE = "data/data_mubawab.csv"


def clean_text(text):
    return " ".join(text.split()) if text else None


def clean_price(text):
    if not text:
        return None
    if "Prix à consulter" in text:
        return None
    nums = re.findall(r"\d+", text.replace(" ", ""))
    return int("".join(nums)) if nums else None


def extract_surface(text):
    if not text:
        return None

    match = re.search(r"(\d+)\s*(?:m²|m2)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_rooms(text):
    if not text:
        return None

    match = re.search(r"(\d+)\s*Pièces?", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_bedrooms(text):
    if not text:
        return None

    match = re.search(r"(\d+)\s*Chambres?", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_bathrooms(text):
    if not text:
        return None

    match = re.search(r"(\d+)\s*Salles?\s+de\s+bains?", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")

    # Pour mode invisible :
    # options.add_argument("--headless=new")

    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )


def load_existing_urls():
    if Path(OUTPUT_FILE).exists():
        df_old = pd.read_csv(OUTPUT_FILE)
        if "url" in df_old.columns:
            return set(df_old["url"].dropna().unique())
    return set()


def save_batch(data):
    if not data:
        return

    df = pd.DataFrame(data)
    file_exists = Path(OUTPUT_FILE).exists()

    df.to_csv(
        OUTPUT_FILE,
        mode="a",
        header=not file_exists,
        index=False,
        encoding="utf-8-sig"
    )


def scrape_mubawab_casa_vente(max_pages=300, start_page=1):
    driver = create_driver()
    existing_urls = load_existing_urls()

    print(f"[INFO] URLs déjà collectées: {len(existing_urls)}")

    total_new = 0

    try:
        for page in range(start_page, max_pages + 1):

            url = BASE_URL if page == 1 else f"{BASE_URL}:p:{page}"

            print(f"\n[INFO] Scraping page {page}: {url}")

            driver.get(url)
            time.sleep(random.uniform(4, 7))

            soup = BeautifulSoup(driver.page_source, "html.parser")

            cards = soup.find_all("li", class_=lambda x: x and "listingBox" in x)

            if not cards:
                cards = soup.find_all("div", class_=lambda x: x and "listingBox" in x)

            print(f"[INFO] Cartes trouvées: {len(cards)}")

            page_data = []

            for card in cards:
                text = clean_text(card.get_text(" "))

                if not text:
                    continue

                link_tag = card.find("a", href=True)
                if not link_tag:
                    continue

                href = link_tag["href"]
                full_url = href if href.startswith("http") else "https://www.mubawab.ma" + href

                if full_url in existing_urls:
                    continue

                title_tag = card.find(["h2", "h3"])
                title = clean_text(title_tag.get_text(" ")) if title_tag else None

                price_tag = card.find(
                    string=lambda s: s and ("DH" in s or "Prix à consulter" in s)
                )
                price_text = clean_text(price_tag) if price_tag else None
                price = clean_price(price_text)

                surface = extract_surface(text)
                rooms = extract_rooms(text)
                bedrooms = extract_bedrooms(text)
                bathrooms = extract_bathrooms(text)

                location = None
                location_match = re.search(
                    r"à\s+([A-Za-zÀ-ÿ\s\-']+),\s*Casablanca",
                    text
                )
                if location_match:
                    location = location_match.group(1).strip()

                item = {
                    "source": "mubawab",
                    "transaction": "vente",
                    "city": "Casablanca",
                    "page": page,
                    "title": title,
                    "price_text": price_text,
                    "price_dh": price,
                    "surface_m2": surface,
                    "price_m2": round(price / surface, 2) if price and surface else None,
                    "rooms": rooms,
                    "bedrooms": bedrooms,
                    "bathrooms": bathrooms,
                    "location": location,
                    "url": full_url,
                    "raw_text": text
                }

                page_data.append(item)
                existing_urls.add(full_url)

            save_batch(page_data)

            total_new += len(page_data)

            print(f"[OK] Page {page}: {len(page_data)} nouvelles annonces")
            print(f"[TOTAL] Nouvelles annonces: {total_new}")

            if len(page_data) == 0:
                print("[STOP] Aucune nouvelle annonce trouvée.")
                break

            time.sleep(random.uniform(2, 5))

    finally:
        driver.quit()

    print("\n[DONE] Scraping terminé.")
    print(f"[CSV] Fichier généré: {OUTPUT_FILE}")


scrape_mubawab_casa_vente(max_pages=100, start_page=1)