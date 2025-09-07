import requests
from bs4 import BeautifulSoup
from openai import OpenAI

import time
from datetime import datetime
import os
from dotenv import load_dotenv
import logging
import hashlib
import re

SECTION_END_MARKERS = [
    "Załączniki",
    "MESSAGE (ENGLISH VERSION)",
    "INFORMACJE O PODMIOCIE",
    "PODPISY OSÓB REPREZENTUJĄCYCH SPÓŁKĘ",
    "PODPISY",
]

class ESPIMonitor:
    model = "gpt-4o-mini"
    system_prompt = """
    Jesteś analitykiem giełdowym. Twoim zadaniem jest ocenić komunikat giełdowy (ESPI). 
    Ocenę wyrażasz jako liczbę całkowitą od -5 do 5:
    -5 oznacza bardzo negatywny wpływ na kurs akcji,
    0 oznacza neutralny,
    5 oznacza bardzo pozytywny (np. nowe kontrakty, znaczące zyski).  

    Odpowiadaj wyłącznie w formacie JSON: 
    {
      "ocena": <liczba od -5 do 5>,
      "uzasadnienie": "<krótkie uzasadnienie oceny>"
    }
    """
    def __init__(self):
        # Załaduj zmienne środowiskowe
        load_dotenv()

        # Konfiguracja logowania - proste bez rotacji
        # Ale tylko INFO i wyższe poziomy (bez DEBUG)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('espi_monitor.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        # URL strony ESPI
        self.url = "https://espiebi.pap.pl/"
        self.client = OpenAI(api_key=os.getenv('OPENAI_API', ''))

        # Obserwowane spółki z pliku .env
        watched_companies_str = os.getenv('WATCHED_COMPANIES', '')
        self.watched_companies = [company.strip().upper() for company in
                                  watched_companies_str.split(',') if company.strip()]

        if not self.watched_companies:
            self.logger.warning(
                "Brak obserwowanych spółek w pliku .env. Dodaj WATCHED_COMPANIES=Dekpol,InnaSpółka")

        # Przechowywanie hashy poprzednich wpisów
        self.previous_entries = set()

        # Konfiguracja sesji HTTP
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        self.logger.info(
            f"Monitor ESPI uruchomiony. Obserwowane spółki: {', '.join(self.watched_companies)}")

    def fetch_page(self):
        """Pobiera zawartość strony ESPI"""
        try:
            response = self.session.get(self.url, timeout=10)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            self.logger.error(f"Błąd podczas pobierania strony: {e}")
            return None

    def pobierz_komunikat_espiebi(self, url: str) -> dict:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        full_text = soup.get_text("\n", strip=True)

        # --- TEMAT ---
        temat = None
        m = re.search(r"Temat\s*[:\-]?\s*(.+)", full_text)
        if m:
            temat = m.group(1).strip()
        else:
            h1 = soup.find("h1")
            if h1:
                temat = h1.get_text(strip=True)

        # --- TREŚĆ ---
        tresc = None
        m = re.search(r"Treść raportu:\s*(.+)", full_text, re.S)
        if m:
            tresc = m.group(1).strip()
            # przytnij na pierwszym markerze
            for marker in SECTION_END_MARKERS:
                idx = tresc.find(marker)
                if idx != -1:
                    tresc = tresc[:idx].strip()
                    break

        return {"temat": temat, "tresc": tresc}

    def parse_entries(self, html_content):
        """Parsuje HTML i wyciąga wpisy ESPI"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            entries = []

            # Szukamy elementów li.news (struktura ESPI)
            news_items = soup.find_all('li', class_='news')

            self.logger.info(f"Znaleziono {len(news_items)} elementów li.news")

            for item in news_items:
                try:
                    # Znajdź link
                    link_elem = item.find('a', class_='link')
                    if not link_elem:
                        continue

                    title = link_elem.get_text(strip=True)
                    link = link_elem.get('href', '')

                    # Znajdź wszystkie div.hour dla daty
                    hour_divs = item.find_all('div', class_='hour')

                    # Wyciągnij czas z pierwszego div.hour i datę/numer z drugiego
                    time_str = ""
                    date_info = ""

                    if len(hour_divs) >= 1:
                        time_str = hour_divs[0].get_text(strip=True)
                    if len(hour_divs) >= 2:
                        date_info = hour_divs[1].get_text(strip=True)

                    # Stwórz datę - użyj dzisiejszej daty + czas z ESPI
                    if time_str and re.match(r'\d{1,2}:\d{2}', time_str):
                        today = datetime.now().strftime('%Y-%m-%d')
                        full_date = f"{today} {time_str}"
                    else:
                        full_date = "Nie znaleziono daty"

                    # Uzupełnij link
                    if link and not link.startswith('http'):
                        if link.startswith('/'):
                            link = 'https://espiebi.pap.pl' + link
                        else:
                            link = 'https://espiebi.pap.pl/' + link

                    dane = self.pobierz_komunikat_espiebi(link)

                    if title and link:
                        entries.append({
                            'title': title,
                            'link': link,
                            'date': full_date,
                            'time_raw': time_str,
                            'date_info_raw': date_info,
                            'report': dane['temat'],
                            'details': dane['tresc']
                        })

                except Exception as e:
                    self.logger.debug(f"Błąd parsowania elementu: {e}")
                    continue

            self.logger.info(f"Sparsowano {len(entries)} wpisów")
            return entries

        except Exception as e:
            self.logger.error(f"Błąd podczas parsowania HTML: {e}")
            return []

    def check_company_match(self, title):
        """Sprawdza czy tytuł zawiera nazwę obserwowanej spółki"""
        title_upper = title.upper()

        for company in self.watched_companies:
            company_upper = company.upper()
            if company_upper in title_upper:
                return company
        return None

    def generate_entry_hash(self, entry):
        """Generuje hash wpisu"""
        content = f"{entry['title']}{entry['link']}"
        return hashlib.md5(content.encode()).hexdigest()

    def process_entries(self, entries):
        """Przetwarza wpisy i sprawdza nowe oraz obserwowane spółki"""
        new_matches = []
        current_hashes = set()

        for entry in entries:
            entry_hash = self.generate_entry_hash(entry)
            current_hashes.add(entry_hash)

            # Sprawdź czy nowy wpis
            is_new = entry_hash not in self.previous_entries

            if is_new:
                # Sprawdź czy dotyczy obserwowanej spółki
                matched_company = self.check_company_match(entry['title'])
                if matched_company:
                    new_matches.append({
                        'company': matched_company,
                        'title': entry['title'],
                        'link': entry['link'],
                        'date': entry['date'],
                        'report': entry['report'],
                        'details': entry['details']
                    })

        # Aktualizuj poprzednie wpisy
        self.previous_entries = current_hashes
        return new_matches

    def display_matches(self, matches):

        """Wyświetla dopasowania w konsoli"""
        for match in matches:
            temat = match['report']
            tresc = match['details']
            completion = self.client.chat.completions.create(
                model=self.model,
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Temat: {temat}\nTreść: {tresc}"}
                ]
            )


            print("\n" + "=" * 80)
            print(f"🚨 NOWY RAPORT ESPI - {match['company']}")
            print(f"📋 Nagłówek: {match['title']}")
            print(f"🔗 Link: {match['link']}")
            print(f"📅 Data ESPI: {match['date']}")
            print(f"📋 Temat: {match['report']}")
            print(f"📋 Treść: {match['details']}")
            print(f"⏰ Wykryto: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📋 OCENA AI: {completion.choices[0].message.content}")
            print("=" * 80 + "\n")

    def run_once(self):
        """Jednorazowe sprawdzenie"""
        self.logger.info("Sprawdzam stronę ESPI...")

        html_content = self.fetch_page()
        if not html_content:
            return

        entries = self.parse_entries(html_content)
        if not entries:
            self.logger.warning("Nie znaleziono wpisów")
            return

        matches = self.process_entries(entries)

        if matches:
            self.display_matches(matches)
            self.logger.info(f"Znaleziono {len(matches)} nowych raportów")
        else:
            self.logger.info("Brak nowych raportów dla obserwowanych spółek")

    def run(self):
        """Główna pętla monitorowania"""
        self.logger.info("Uruchamiam monitor ESPI. Ctrl+C aby zatrzymać.")

        # Pierwsze uruchomienie - załaduj obecne wpisy
        html_content = self.fetch_page()
        if html_content:
            entries = self.parse_entries(html_content)

            matches = self.process_entries(entries)
            if matches:
                self.display_matches(matches)

            for entry in entries:
                entry_hash = self.generate_entry_hash(entry)
                self.previous_entries.add(entry_hash)
            self.logger.info("Załadowano istniejące wpisy")

        try:
            while True:
                self.run_once()
                self.logger.info("Czekam 60 sekund...")
                time.sleep(60)
        except KeyboardInterrupt:
            self.logger.info("Monitor zatrzymany")
        except Exception as e:
            self.logger.error(f"Błąd: {e}")


if __name__ == "__main__":
    monitor = ESPIMonitor()
    monitor.run()