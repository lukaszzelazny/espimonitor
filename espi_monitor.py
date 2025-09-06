import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import os
from dotenv import load_dotenv
import logging
import hashlib


class ESPIMonitor:
    def __init__(self):
        # Załaduj zmienne środowiskowe
        load_dotenv()

        # Konfiguracja logowania
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

        # Obserwowane spółki z pliku .env
        watched_companies_str = os.getenv('WATCHED_COMPANIES', '')
        self.watched_companies = [company.strip().upper() for company in
                                  watched_companies_str.split(',') if company.strip()]

        if not self.watched_companies:
            self.logger.warning(
                "Brak obserwowanych spółek w pliku .env. Dodaj WATCHED_COMPANIES=Dekpol,InnaSpółka")

        # Przechowywanie hashy poprzednich wpisów dla wykrywania nowych
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

    def parse_entries(self, html_content):
        """Parsuje HTML i wyciąga wpisy ESPI"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Szukamy kontenerów z wpisami ESPI
            # Na podstawie struktury strony, wpisy są prawdopodobnie w divach lub li
            entries = []

            # Próbujemy różne selektory w zależności od struktury strony
            possible_selectors = [
                '.espi-item', '.espi-entry', '.report-item',
                'div[class*="espi"]', 'li[class*="espi"]',
                'tr', 'div.item', '.news-item'
            ]

            for selector in possible_selectors:
                elements = soup.select(selector)
                if elements:
                    self.logger.info(f"Znaleziono elementy z selektorem: {selector}")
                    break

            # Jeśli nie znajdziemy specyficznych selektorów, szukamy linków
            if not elements:
                # Szukamy wszystkich linków, które mogą prowadzić do raportów
                elements = soup.find_all('a', href=True)

            for element in elements:
                try:
                    # Wyciągnij tytuł
                    title = ""
                    if element.get_text(strip=True):
                        title = element.get_text(strip=True)
                    elif element.get('title'):
                        title = element.get('title')

                    # Wyciągnij link
                    link = ""
                    if element.name == 'a':
                        link = element.get('href', '')
                    else:
                        # Szukaj linka wewnątrz elementu
                        link_elem = element.find('a', href=True)
                        if link_elem:
                            link = link_elem.get('href', '')
                            if not title:
                                title = link_elem.get_text(strip=True)

                    # Uzupełnij względny link do pełnego URL
                    if link and not link.startswith('http'):
                        if link.startswith('/'):
                            link = 'https://espiebi.pap.pl' + link
                        else:
                            link = 'https://espiebi.pap.pl/' + link

                    # Dodaj wpis jeśli ma tytuł i link
                    if title and link and len(
                            title) > 10:  # Filtruj bardzo krótkie teksty
                        entries.append({
                            'title': title,
                            'link': link
                        })

                except Exception as e:
                    self.logger.debug(f"Błąd podczas parsowania elementu: {e}")
                    continue

            self.logger.info(f"Znaleziono {len(entries)} wpisów")
            return entries[:20]  # Ograniczamy do 20 najnowszych wpisów

        except Exception as e:
            self.logger.error(f"Błąd podczas parsowania HTML: {e}")
            return []

    def check_company_match(self, title):
        """Sprawdza czy tytuł zawiera nazwę obserwowanej spółki"""
        title_upper = title.upper()

        for company in self.watched_companies:
            if company in title_upper:
                return company
        return None

    def generate_entry_hash(self, entry):
        """Generuje hash wpisu do wykrywania duplikatów"""
        content = f"{entry['title']}{entry['link']}"
        return hashlib.md5(content.encode()).hexdigest()

    def process_entries(self, entries):
        """Przetwarza wpisy i sprawdza czy są nowe oraz czy dotyczą obserwowanych spółek"""
        new_matches = []
        current_hashes = set()

        for entry in entries:
            entry_hash = self.generate_entry_hash(entry)
            current_hashes.add(entry_hash)

            # Sprawdź czy to nowy wpis
            if entry_hash not in self.previous_entries:
                # Sprawdź czy dotyczy obserwowanej spółki
                matched_company = self.check_company_match(entry['title'])
                if matched_company:
                    new_matches.append({
                        'company': matched_company,
                        'title': entry['title'],
                        'link': entry['link']
                    })

        # Aktualizuj zestaw poprzednich wpisów
        self.previous_entries = current_hashes

        return new_matches

    def display_matches(self, matches):
        """Wyświetla znalezione dopasowania w konsoli"""
        for match in matches:
            print("\n" + "=" * 80)
            print(f"🚨 NOWY RAPORT ESPI - {match['company']}")
            print(f"📋 Tytuł: {match['title']}")
            print(f"🔗 Link: {match['link']}")
            print(f"⏰ Czas: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 80 + "\n")

    def run_once(self):
        """Jednorazowe sprawdzenie strony"""
        self.logger.info("Rozpoczynam sprawdzanie strony ESPI...")

        html_content = self.fetch_page()
        if not html_content:
            return

        entries = self.parse_entries(html_content)
        if not entries:
            self.logger.warning(
                "Nie znaleziono żadnych wpisów. Możliwe, że struktura strony się zmieniła.")
            return

        matches = self.process_entries(entries)

        if matches:
            self.display_matches(matches)
            self.logger.info(
                f"Znaleziono {len(matches)} nowych raportów dla obserwowanych spółek")
        else:
            self.logger.info("Brak nowych raportów dla obserwowanych spółek")

    def run(self):
        """Główna pętla monitorowania"""
        self.logger.info("Uruchamiam monitor ESPI. Naciśnij Ctrl+C aby zatrzymać.")

        # Pierwsze uruchomienie - ładujemy aktualne wpisy bez powiadomień
        html_content = self.fetch_page()
        if html_content:
            entries = self.parse_entries(html_content)

            ####
            matches = self.process_entries(entries)
            if matches:
                self.display_matches(matches)

            #####

            for entry in entries:
                entry_hash = self.generate_entry_hash(entry)
                self.previous_entries.add(entry_hash)
            self.logger.info("Załadowano aktualne wpisy jako punkt odniesienia")

        try:
            while True:
                self.run_once()
                self.logger.info("Czekam 60 sekund do następnego sprawdzenia...")
                time.sleep(60)  # Czekaj minutę

        except KeyboardInterrupt:
            self.logger.info("Monitor zatrzymany przez użytkownika")
        except Exception as e:
            self.logger.error(f"Nieoczekiwany błąd: {e}")

    def test_entry(self, title, link):
        """Testowa metoda do sprawdzenia konkretnego wpisu"""
        print(f"\n🧪 TEST WPISU:")
        print(f"Tytuł: {title}")
        print(f"Link: {link}")
        print(f"Obserwowane spółki: {self.watched_companies}")

        matched_company = self.check_company_match(title)
        if matched_company:
            print(f"✅ DOPASOWANIE: {matched_company}")
            self.display_matches([{
                'company': matched_company,
                'title': title,
                'link': link
            }])
        else:
            print("❌ Brak dopasowania")
        print()


if __name__ == "__main__":
    monitor = ESPIMonitor()
    monitor.run()