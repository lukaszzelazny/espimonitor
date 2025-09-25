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
import multiprocessing
from dane_finansowe import pobierz_raport_finansowy_espi

load_dotenv()

TOKEN = os.getenv("TG_BOT_TOKEN")      # ustaw w ENV: TG_BOT_TOKEN
CHAT_ID = os.getenv("TG_CHAT_ID")      # ustaw w ENV: TG_CHAT_ID

SECTION_END_MARKERS = [
    "Załączniki",
    "MESSAGE (ENGLISH VERSION)",
    "INFORMACJE O PODMIOCIE",
    "PODPISY OSÓB REPREZENTUJĄCYCH SPÓŁKĘ",
    "PODPISY",
]

FINANCIAL_SECTION_END_MARKERS = [
    "Podpisy osób odpowiedzialnych",
    "Podpisy członków zarządu",
    "Załączniki",
    "Dodatkowe informacje",
    "Komentarz zarządu",
    "Oświadczenia",
    "Data sporządzenia",
    "Warszawa, dnia"
]

class ESPIMonitor:
    model = "gpt-4o-mini"
    system_prompt = """
    Jesteś analitykiem giełdowym. Twoim zadaniem jest ocenić komunikat giełdowy (ESPI) pod kątem jego krótkoterminowego wpływu na kurs akcji spółki.
    
    Skup się przede wszystkim na informacjach, które realnie mogą wpływać na notowania: nowe kontrakty, znaczący klient, wyniki finansowe, istotne zmiany strategii, wezwania, kryzysy, istotne inwestycje lub partnerstwa. 
    Traktuj komunikaty formalne, administracyjne i techniczne (np. rejestracja akcji, dopuszczenie do obrotu, zmiany w radzie nadzorczej, zgody KNF) jako neutralne, chyba że niosą dodatkowe znaczenie biznesowe.
    
    Ocenę wyrażasz jako liczbę całkowitą od -5 do 5:
    -5 = bardzo negatywny wpływ na kurs (np. duża strata, utrata kontraktu, problemy prawne),
    0 = neutralny (np. sprawy formalne, zmiany techniczne bez wpływu na biznes),
    +5 = bardzo pozytywny (np. przełomowy kontrakt, znaczący wzrost zysków, strategiczne partnerstwo).
    
    Odpowiadaj wyłącznie w formacie JSON:
    {
      "ocena": <liczba od -5 do 5>,
      "uzasadnienie": "<krótkie uzasadnienie oceny>"
    }

    """
    report_prompt = """Jesteś doświadczonym analitykiem giełdowym specjalizującym się w analizie raportów finansowych spółek publicznych. 
Twoim zadaniem jest ocena wpływu przedstawionych danych finansowych na prawdopodobny kierunek kursu akcji spółki.

KRYTERIA OCENY:
- Analizuj kluczowe wskaźniki: przychody, rentowność, przepływy pieniężne, zadłużenie, sytuację bilansową
- Porównuj dane rok do roku (dynamikę zmian)
- Uwzględniaj trendy i stabilność wyników
- Oceniaj w kontekście oczekiwań rynkowych i kondycji sektora

SKALA OCENY:
-5: Bardzo negatywny wpływ na kurs (poważne problemy finansowe, drastyczny spadek wyników)
-4: Silnie negatywny (wyraźne pogorszenie wyników, niepokojące trendy)
-3: Negatywny (spadek kluczowych wskaźników, słabe wyniki)
-2: Lekko negatywny (niewielkie pogorszenie lub mieszane sygnały z przewagą negatywnych)
-1: Słabo negatywny (stabilne wyniki z drobnymi negatywnymi aspektami)
0: Neutralny (brak znaczących zmian, wyniki zgodne z oczekiwaniami)
1: Słabo pozytywny (stabilne wyniki z drobnymi pozytywnymi aspektami)
2: Lekko pozytywny (niewielka poprawa lub mieszane sygnały z przewagą pozytywnych)
3: Pozytywny (wzrost kluczowych wskaźników, dobre wyniki)
4: Silnie pozytywny (wyraźna poprawa wyników, pozytywne trendy)
5: Bardzo pozytywny wpływ na kurs (wybitne wyniki, znaczący wzrost rentowności)

ODPOWIADAJ WYŁĄCZNIE W FORMACIE JSON:
{
    "ocena": <liczba od -5 do 5>,
    "uzasadnienie": "<zwięzłe uzasadnienie oceny w 2-3 zdaniach, skupiające się na najważniejszych czynnikach>"
}"""
    def __init__(self):
        # Załaduj zmienne środowiskowe


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
        self.url = "https://espiebi.pap.pl/?page=0"
        self.client = OpenAI(api_key=os.getenv('OPENAI_API', ''))
        self.checkConstants = ['RAPORT']

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

                    isReport = False
                    title = link_elem.get_text(strip=True)
                    upperTitle = title.upper()

                    for const in self.checkConstants:
                        if const in upperTitle:
                            isReport = True
                            break

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

                    dane = pobierz_raport_finansowy_espi(link) if isReport \
                        else self.pobierz_komunikat_espiebi(link)

                    if isReport:
                        entries.append({
                            'title': title,
                            'link': link,
                            'date': full_date,
                            'time_raw': time_str,
                            'date_info_raw': date_info,
                            'report': dane['temat'],
                            'details': dane['dane_finansowe'],
                            'isReport': True
                        })

                    elif title and link:
                        entries.append({
                            'title': title,
                            'link': link,
                            'date': full_date,
                            'time_raw': time_str,
                            'date_info_raw': date_info,
                            'report': dane['temat'],
                            'details': dane['tresc'],
                            'isReport': False
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

        for const in self.checkConstants:
            if const in title_upper:
                return True

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
                        'company': entry['title'] if matched_company is True else matched_company,
                        'title': entry['title'],
                        'link': entry['link'],
                        'date': entry['date'],
                        'report': entry['report'],
                        'details': entry['details'],
                        'isReport': entry['isReport']
                    })

        # Aktualizuj poprzednie wpisy
        self.previous_entries = current_hashes
        return new_matches

    def display_matches(self, matches):
        """Wyświetla dopasowania w konsoli i wysyła na Telegram"""
        for match in matches:
            temat = match['report']
            tresc = match['details']
            isReport = match['isReport']
            if isReport:
                user_content = f"""TEMAT RAPORTU: {temat}

                DANE FINANSOWE (JSON):
                {tresc}

                Przeanalizuj powyższe dane finansowe jak doświadczony analityk giełdowy i oceń prawdopodobny wpływ na kurs akcji spółki."""
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.report_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.3,
                    # Niższa temperatura dla bardziej konsystentnych analiz
                    max_tokens=500  # Ograniczenie dla zwięzłej odpowiedzi
                )
            else:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": f"Temat: {temat}\nTreść: {tresc}"}
                    ]
                )

            # Wyświetlenie w konsoli
            print("\n" + "=" * 80)
            print(f"🚨 NOWY RAPORT ESPI - {match['company']}")
            print(f"📋 Nagłówek: {match['title']}")
            print(f"🔗 Link: {match['link']}")
            print(f"📅 Data ESPI: {match['date']}")
            print(f"📋 Temat: {match['report']}")
            print(f"⏰ Wykryto: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📋 OCENA AI: {completion.choices[0].message.content}")
            print("=" * 80 + "\n")

            # Przygotowanie wiadomości Telegram w formacie HTML
            ai_ocena = completion.choices[0].message.content
            telegram_message = f"""🚨 <b>NOWY RAPORT ESPI - {match['company']}</b>

    📋 <b>Nagłówek:</b> {match['title']}

    🔗 <b>Link:</b> <a href="{match['link']}">Zobacz raport</a>

    📅 <b>Data ESPI:</b> {match['date']}

    📋 <b>Temat:</b> {temat}

    ⏰ <b>Wykryto:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    🤖 <b>OCENA AI:</b>
    {ai_ocena}"""

            # Wysłanie wiadomości na Telegram
            send_telegram_message(telegram_message)

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

def send_telegram_message(text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f"[TG] Błąd wysyłki: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[TG] Wyjątek przy wysyłce: {e}")

if __name__ == "__main__":
    print("Starting Telegram bot...")
    try:
        # Uruchom bota w osobnym procesie
        send_telegram_message(
            f"🟢 Bot działa i będzie monitorował ESPI.")
        monitor = ESPIMonitor()
        bot_process = multiprocessing.Process(target=monitor.run())
        bot_process.start()
        print(f"Bot process started with PID: {bot_process.pid}")

    except KeyboardInterrupt:
        print("Przerwano ręcznie.")
        if bot_process.is_alive():
            bot_process.terminate()
            bot_process.join()