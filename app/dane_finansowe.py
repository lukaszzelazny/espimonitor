import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, Optional


# Markery końca sekcji - możesz dostosować do struktury raportów
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


def pobierz_raport_finansowy_espi(url: str) -> Dict[str, Optional[str]]:
    """
    Pobiera raport finansowy ze strony ESPI/EBI i zwraca dane przygotowane
    do analizy przez ChatGPT API.

    Args:
        url: URL raportu na stronie ESPI

    Returns:
        dict: Słownik z kluczami:
            - temat: tytuł raportu
            - typ_raportu: typ raportu (np. "Skonsolidowany raport półroczny")
            - okres: okres sprawozdawczy
            - spolka: nazwa spółki
            - tresc: główna treść raportu
            - dane_finansowe: wyekstraktowane dane liczbowe (obecny i poprzedni okres)
            - dodatkowe_info: dodatkowe informacje kontekstowe
    """
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    full_text = soup.get_text("\n", strip=True)

    # --- PODSTAWOWE INFORMACJE ---
    temat = None
    typ_raportu = None
    okres = None
    spolka = None

    # Temat - szukaj w różnych miejscach
    temat_patterns = [
        r"Temat\s*[:\-]?\s*(.+?)(?:\n|$)",
        r"Rodzaj\s+raportu\s*[:\-]?\s*(.+?)(?:\n|$)",
        r"<title>([^<]+)</title>"  # z tagu HTML
    ]

    for pattern in temat_patterns:
        if pattern.startswith("<title>"):
            m = re.search(pattern, r.text, re.I)
        else:
            m = re.search(pattern, full_text, re.I)
        if m:
            temat_candidate = m.group(1).strip()
            # Sprawdź czy to nie jest tylko "ESPI/EBI"
            if len(temat_candidate) > 10 and "ESPI" not in temat_candidate:
                temat = temat_candidate
                break

    # Jeśli nadal nie ma tematu, spróbuj z nagłówka
    if not temat:
        h1 = soup.find("h1")
        if h1:
            temat = h1.get_text(strip=True)

    # Typ raportu na podstawie tematu
    if temat:
        temat_lower = temat.lower()
        if "skonsolidowany" in temat_lower and "półroczny" in temat_lower:
            typ_raportu = "Skonsolidowany raport półroczny"
        elif "półroczny" in temat_lower:
            typ_raportu = "Raport półroczny"
        elif "roczny" in temat_lower:
            typ_raportu = "Raport roczny"
        elif "kwartalny" in temat_lower:
            typ_raportu = "Raport kwartalny"

    # Okres sprawozdawczy - bardziej precyzyjne wzorce
    okres_patterns = [
        r"za\s+okres\s+(\d{1,2}\s+miesięcy\s+zakończony\s+\d{1,2}\.\d{1,2}\.\d{4})",
        r"za\s+(\d{1,2}\s+miesięcy\s+\d{4})",
        r"okres\s*[:\-]?\s*(\d{1,2}\.\d{1,2}\.\d{4}\s*[\-–]\s*\d{1,2}\.\d{1,2}\.\d{4})",
        r"za\s+rok\s+(\d{4})",
        r"(\d{4})\s*r\.?"
    ]
    for pattern in okres_patterns:
        m = re.search(pattern, full_text, re.I)
        if m:
            okres = m.group(1).strip()
            break

    # Nazwa spółki - ulepszone wzorce
    spolka_patterns = [
        r"(?:Emitent|Spółka)\s*[:\-]?\s*([A-ZĄĆĘŁŃÓŚŹŻ\s&\.]+(?:S\.A\.|SA))",
        r"Nazwa\s+emitenta\s*[:\-]?\s*([A-ZĄĆĘŁŃÓŚźż\s&\.]+(?:S\.A\.|SA))",
        r"([A-ZĄĆĘŁŃÓŚŹŻ\s&\.]+(?:S\.A\.|SA))\s*(?:Skonsolidowany|Raport|za)",
        r"^([A-ZĄĆĘŁŃÓŚŹŻ\s&\.]{5,})\s*$"  # linia z samą nazwą spółki
    ]

    for pattern in spolka_patterns:
        matches = re.findall(pattern, full_text, re.I | re.M)
        for match in matches:
            if len(match.strip()) > 3 and not any(skip in match.lower()
                                                  for skip in
                                                  ['serwis', 'espi', 'ebi', 'raport']):
                spolka = match.strip()
                break
        if spolka:
            break

    # --- GŁÓWNA TREŚĆ RAPORTU ---
    tresc = None
    tresc_patterns = [
        r"Treść raportu:\s*(.+)",
        r"RAPORT\s+(?:FINANSOWY|PÓŁROCZNY|ROCZNY|KWARTALNY)\s*(.+)",
        r"I\.\s*DANE\s+(?:FINANSOWE|SPRAWOZDAWCZE)\s*(.+)",
    ]

    for pattern in tresc_patterns:
        m = re.search(pattern, full_text, re.S | re.I)
        if m:
            tresc = m.group(1).strip()
            break

    # Jeśli nie znaleziono wzorców, weź większą część tekstu
    if not tresc:
        lines = full_text.split('\n')
        # Znajdź linię startową (po podstawowych danych)
        start_idx = 0
        for i, line in enumerate(lines):
            if any(keyword in line.lower() for keyword in
                   ['wybrane dane', 'wyniki finansowe', 'sprawozdanie', 'bilans']):
                start_idx = i
                break
        tresc = '\n'.join(lines[start_idx:])

    # Przytnij treść na markerach końca
    if tresc:
        for marker in FINANCIAL_SECTION_END_MARKERS:
            idx = tresc.find(marker)
            if idx != -1:
                tresc = tresc[:idx].strip()
                break

    dane_finansowe_raw = ("WYBRANE DANE FINANSOWE\n"
                          + full_text.split("WYBRANE DANE FINANSOWE", 2)[2].
                          split("INFORMACJA O KOREKCIE RAPORTU")[0])

    # --- EKSTRAKCJA DANYCH FINANSOWYCH ---
    dane_finansowe = ekstraktuj_dane_finansowe(dane_finansowe_raw)


    return {
        "temat": temat,
        "typ_raportu": typ_raportu,
        "okres": okres,
        "spolka": spolka,
        "tresc": tresc,
        "dane_finansowe": dane_finansowe  # dla debugowania
    }


def ekstraktuj_dane_finansowe(text: str) -> Dict[str, Dict]:
    """Ekstraktuje kluczowe dane finansowe z tekstu raportu z analizą rok do roku."""
    dane = {
        'obecny_okres': {},
        'poprzedni_okres': {},
        'analiza_rr': {},  # porównanie rok do roku
        'debug_info': []
    }

    def clean_number(num_str: str) -> float:
        """Czyści i normalizuje liczbę z tekstu, zwraca jako float."""
        if not num_str or num_str == '-' or num_str.strip() == '':
            return 0.0

        # Usuń wszystkie spacje i połącz cyfry
        cleaned = num_str.strip()

        # Obsługa liczb ujemnych w nawiasach
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]

        # Usuń spacje między cyframi (np. "921 703" -> "921703")
        import re
        # Znajdź wszystkie cyfry i połącz je, zachowując przecinki i kropki
        parts = re.findall(r'[\d\s,.-]+', cleaned)
        if parts:
            cleaned = parts[0]
            # Usuń spacje między cyframi
            cleaned = re.sub(r'(\d)\s+(\d)', r'\1\2', cleaned)
            # Zamień przecinek na kropkę dla separatora dziesiętnego
            if ',' in cleaned and '.' not in cleaned:
                cleaned = cleaned.replace(',', '.')

        # Usuń wszystkie pozostałe znaki oprócz cyfr, kropek i minusów
        cleaned = re.sub(r'[^\d\.\-]', '', cleaned)

        if not cleaned or cleaned in ['-', '+']:
            return 0.0

        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def oblicz_zmiane_rr(obecny: float, poprzedni: float) -> Dict[str, any]:
        """Oblicza zmianę rok do roku - bezwzględną i procentową."""
        if poprzedni == 0:
            if obecny == 0:
                return {'zmiana_bezwzgledna': 0.0, 'zmiana_procentowa': 0.0,
                        'trend': 'bez_zmian'}
            else:
                return {'zmiana_bezwzgledna': obecny, 'zmiana_procentowa': float('inf'),
                        'trend': 'nowy'}

        zmiana_bezwzgledna = obecny - poprzedni
        zmiana_procentowa = (zmiana_bezwzgledna / abs(poprzedni)) * 100

        # Określ trend
        if zmiana_procentowa > 5:
            trend = 'wzrost'
        elif zmiana_procentowa < -5:
            trend = 'spadek'
        else:
            trend = 'stabilny'

        return {
            'zmiana_bezwzgledna': round(zmiana_bezwzgledna, 2),
            'zmiana_procentowa': round(zmiana_procentowa, 2),
            'trend': trend
        }

    # Mapa pozycji finansowych do ich kluczy
    financial_items_map = {
        # Przychody i koszty
        'Przychody ze sprzedaży': 'przychody_sprzedazy',
        'Przychody z działalności operacyjnej': 'przychody_operacyjne',
        'Koszty działalności operacyjnej': 'koszty_operacyjne',

        # Zyski
        'Zysk na działalności operacyjnej': 'zysk_operacyjny',
        'Zysk (strata) na działalności operacyjnej': 'zysk_operacyjny',
        'Zysk brutto': 'zysk_brutto',
        'Zysk (strata) przed opodatkowaniem': 'zysk_przed_opodatkowaniem',
        'Zysk netto': 'zysk_netto',
        'Zysk (strata) netto': 'zysk_netto',
        'Zysk (strata) netto przypadający akcjonariuszom jednostki dominującej': 'zysk_netto_akcjonariusze',

        # Zysk na akcję
        'Zysk netto na akcję zwykłą': 'zysk_na_akcje',
        'Zysk (strata) netto na jedną akcję zwykłą': 'zysk_na_akcje',
        'Zysk (strata) netto na jedną średnioważoną akcję zwykłą': 'zysk_na_akcje_sredniowazona',

        # Przepływy pieniężne
        'Przepływy pieniężne netto z działalności operacyjnej': 'przeplyw_operacyjny',
        'Środki pieniężne netto z działalności operacyjnej': 'przeplyw_operacyjny',
        'Przepływy pieniężne netto z działalności inwestycyjnej': 'przeplyw_inwestycyjny',
        'Środki pieniężne netto z działalności inwestycyjnej': 'przeplyw_inwestycyjny',
        'Przepływy pieniężne netto z działalności finansowej': 'przeplyw_finansowy',
        'Środki pieniężne netto wykorzystane w działalności finansowej': 'przeplyw_finansowy',

        # Bilans
        'Aktywa razem': 'aktywa_razem',
        'Suma bilansowa': 'aktywa_razem',
        'Aktywa trwałe': 'aktywa_trwale',
        'Aktywa obrotowe': 'aktywa_obrotowe',
        'Zobowiązania razem': 'zobowiazania_razem',
        'Zobowiązania długoterminowe': 'zobowiazania_dlugoterminowe',
        'Zobowiązania krótkoterminowe': 'zobowiazania_krotkoterminowe',
        'W tym: zobowiązania krótkoterminowe': 'zobowiazania_krotkoterminowe',
        'Kapitał własny': 'kapital_wlasny',
        'Kapitał podstawowy': 'kapital_podstawowy',
        'Wyemitowany kapitał akcyjny': 'wyemitowany_kapital',

        # Akcje
        'Liczba akcji w sztukach': 'liczba_akcji',
        'Liczba akcji (szt.)': 'liczba_akcji',
        'Średnioważona liczba akcji (w szt.)': 'sredniowazona_liczba_akcji',
        'Wartość księgowa na akcję': 'wartosc_ksiegowa_na_akcje',
        'Wartość księgowa na jedną akcję zwykłą': 'wartosc_ksiegowa_na_akcje'
    }

    # Podziel tekst na linie
    lines = text.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        # Sprawdź czy linia to pozycja finansowa
        matched_key = None
        matched_item = None

        for item, key in financial_items_map.items():
            if line == item:
                matched_key = key
                matched_item = item
                break

        if matched_key:
            dane['debug_info'].append(f"Znaleziono pozycję: '{line}' -> {matched_key}")

            # Szukaj wartości w następnych 4 liniach
            values = []
            for j in range(i + 1, min(i + 5, len(lines))):
                next_line = lines[j].strip()

                if not next_line:
                    continue

                # Pomiń linie z nagłówkami
                if any(header in next_line.lower() for header in
                       ['pln', 'eur', 'tys', 'półrocze', 'okres', 'w tym:', 'dane']):
                    continue

                # Jeśli to kolejna pozycja finansowa, przerwij
                if next_line in financial_items_map:
                    break

                # Sprawdź czy to wartość numeryczna
                cleaned_value = clean_number(next_line)

                # Akceptuj wartości (włącznie z zerem)
                if cleaned_value != 0.0 or next_line.strip() in ['0', '-', '(0)']:
                    values.append(cleaned_value)
                    dane['debug_info'].append(
                        f"  Znaleziona wartość: '{next_line}' -> {cleaned_value}")

                # Jeśli mamy już 4 wartości (2 PLN + 2 EUR), możemy się zatrzymać
                if len(values) >= 4:
                    break

            # Zapisz dane jeśli znaleziono co najmniej 2 wartości
            if len(values) >= 2:
                # Pierwsze dwie wartości to PLN: obecny okres, poprzedni okres
                obecny_okres = values[0]
                poprzedni_okres = values[1]

                dane['obecny_okres'][matched_key] = obecny_okres
                dane['poprzedni_okres'][matched_key] = poprzedni_okres
                dane['analiza_rr'][matched_key] = oblicz_zmiane_rr(obecny_okres,
                                                                   poprzedni_okres)

                dane['debug_info'].append(
                    f"ZAPISANO {matched_key}: obecny={obecny_okres}, poprzedni={poprzedni_okres}, "
                    f"zmiana={dane['analiza_rr'][matched_key]['zmiana_procentowa']}% ({dane['analiza_rr'][matched_key]['trend']})"
                )
            else:
                dane['debug_info'].append(
                    f"POMINIĘTO {matched_key}: znaleziono tylko {len(values)} wartości")

        i += 1

    return dane

def wyswietl_dane_finansowe(dane: Dict) -> str:
    """Formatuje dane finansowe do czytelnego wyświetlenia z analizą r/r."""
    output = []

    def format_trend(trend: str) -> str:
        emoji_map = {
            'wzrost': '📈',
            'spadek': '📉',
            'stabilny': '➡️',
            'bez_zmian': '⚫',
            'nowy': '🆕'
        }
        return emoji_map.get(trend, '❓')

    def format_number(value: float) -> str:
        """Formatuje liczbę z odpowiednimi separatorami."""
        if abs(value) >= 1000:
            return f"{value:,.0f}".replace(',', ' ')
        else:
            return f"{value:.2f}"

    def display_section(title: str, section_data: Dict):
        output.append(f"\n=== {title} ===")

        if not section_data['obecny_okres']:
            output.append("Brak danych do wyświetlenia")
            return

        output.append(
            f"\n{'Pozycja':<45} {'H1 2025':<15} {'H1 2024':<15} {'Zmiana':<15} {'%':<10} {'Trend'}")
        output.append("=" * 110)

        for key in section_data['obecny_okres'].keys():
            obecny = section_data['obecny_okres'][key]
            poprzedni = section_data['poprzedni_okres'].get(key, 0)
            analiza = section_data['analiza_rr'].get(key, {})

            zmiana_bezw = analiza.get('zmiana_bezwzgledna', 0)
            zmiana_proc = analiza.get('zmiana_procentowa', 0)
            trend = analiza.get('trend', 'brak')
            trend_emoji = format_trend(trend)

            # Formatowanie liczb
            obecny_str = format_number(obecny)
            poprzedni_str = format_number(poprzedni)
            zmiana_str = f"{zmiana_bezw:+,.0f}".replace(',',
                                                        ' ') if zmiana_bezw else "0"
            proc_str = f"{zmiana_proc:+.1f}%" if zmiana_proc != float('inf') else "N/A"

            key_display = key.replace('_', ' ').replace('sredniowazona',
                                                        'średnioważona').title()

            output.append(
                f"{key_display:<45} {obecny_str:<15} {poprzedni_str:<15} {zmiana_str:<15} {proc_str:<10} {trend_emoji} {trend}")

    display_section("DANE SKONSOLIDOWANE", dane['skonsolidowane'])
    display_section("DANE JEDNOSTKOWE", dane['jednostkowe'])

    # Podsumowanie kluczowych wskaźników
    output.append(
        "\n=== ANALIZA KLUCZOWYCH WSKAŹNIKÓW (SKONSOLIDOWANE H1 2025 vs H1 2024) ===")
    skons = dane['skonsolidowane']

    kluczowe_wskazniki = [
        ('przychody_sprzedazy', 'Przychody ze sprzedaży'),
        ('zysk_operacyjny', 'Zysk operacyjny'),
        ('zysk_netto', 'Zysk netto'),
        ('aktywa_razem', 'Aktywa razem'),
        ('kapital_wlasny', 'Kapitał własny'),
        ('zysk_na_akcje', 'Zysk na akcję')
    ]

    for key, display_name in kluczowe_wskazniki:
        if key in skons['analiza_rr']:
            analiza = skons['analiza_rr'][key]
            trend = analiza['trend']
            trend_emoji = format_trend(trend)
            zmiana_proc = analiza['zmiana_procentowa']

            if zmiana_proc == float('inf'):
                proc_str = "nowa pozycja"
            else:
                proc_str = f"{zmiana_proc:+.1f}%"

            output.append(f"• {display_name}: {proc_str} {trend_emoji}")

    return "\n".join(output)



# Funkcja pomocnicza do wyświetlania wyników
def wyswietl_dane_finansowe(dane: Dict) -> str:
    """Formatuje dane finansowe do czytelnego wyświetlenia z analizą r/r."""
    output = []

    def format_trend(trend: str) -> str:
        emoji_map = {
            'wzrost': '📈',
            'spadek': '📉',
            'stabilny': '➡️',
            'bez_zmian': '⚫',
            'nowy': '🆕'
        }
        return emoji_map.get(trend, '❓')

    def display_section(title: str, section_data: Dict):
        output.append(f"\n=== {title} ===")

        if not section_data['obecny_okres']:
            output.append("Brak danych do wyświetlenia")
            return

        output.append(
            f"\n{'Pozycja':<35} {'2025':<15} {'2024':<15} {'Zmiana':<15} {'%':<10} {'Trend'}")
        output.append("=" * 95)

        for key in section_data['obecny_okres'].keys():
            obecny = section_data['obecny_okres'][key]
            poprzedni = section_data['poprzedni_okres'].get(key, 0)
            analiza = section_data['analiza_rr'].get(key, {})

            zmiana_bezw = analiza.get('zmiana_bezwzgledna', 0)
            zmiana_proc = analiza.get('zmiana_procentowa', 0)
            trend = analiza.get('trend', 'brak')
            trend_emoji = format_trend(trend)

            # Formatowanie liczb (w tys. PLN)
            obecny_str = f"{obecny:,.0f}".replace(',', ' ') if obecny else "0"
            poprzedni_str = f"{poprzedni:,.0f}".replace(',', ' ') if poprzedni else "0"
            zmiana_str = f"{zmiana_bezw:,.0f}".replace(',', ' ') if zmiana_bezw else "0"
            proc_str = f"{zmiana_proc:+.1f}%" if zmiana_proc != float('inf') else "N/A"

            key_display = key.replace('_', ' ').title()

            output.append(
                f"{key_display:<35} {obecny_str:<15} {poprzedni_str:<15} {zmiana_str:<15} {proc_str:<10} {trend_emoji} {trend}")

    display_section("DANE SKONSOLIDOWANE", dane['skonsolidowane'])
    display_section("DANE JEDNOSTKOWE", dane['jednostkowe'])

    # Podsumowanie kluczowych wskaźników
    output.append("\n=== PODSUMOWANIE KLUCZOWYCH WSKAŹNIKÓW (SKONSOLIDOWANE) ===")
    skons = dane['skonsolidowane']

    kluczowe_wskazniki = [
        ('przychody_sprzedazy', 'Przychody ze sprzedaży'),
        ('zysk_operacyjny', 'Zysk operacyjny'),
        ('zysk_netto', 'Zysk netto'),
        ('aktywa_razem', 'Aktywa razem'),
        ('kapital_wlasny', 'Kapitał własny')
    ]

    for key, display_name in kluczowe_wskazniki:
        if key in skons['analiza_rr']:
            analiza = skons['analiza_rr'][key]
            trend = analiza['trend']
            trend_emoji = format_trend(trend)
            zmiana_proc = analiza['zmiana_procentowa']

            if zmiana_proc == float('inf'):
                proc_str = "N/A"
            else:
                proc_str = f"{zmiana_proc:+.1f}%"

            output.append(f"• {display_name}: {proc_str} {trend_emoji}")

    return "\n".join(output)


def ekstraktuj_wskazniki_finansowe(dane_finansowe: Dict) -> Dict[str, str]:
    """Oblicza podstawowe wskaźniki finansowe na podstawie wyekstraktowanych danych."""
    wskazniki = {}

    try:
        obecny = dane_finansowe.get('obecny_okres', {})
        poprzedni = dane_finansowe.get('poprzedni_okres', {})

        # Funkcja pomocnicza do konwersji stringów na liczby
        def to_number(value_str):
            if not value_str or value_str == '-':
                return None
            try:
                # Usuń spacje, przecinki i inne znaki niealfanumeryczne oprócz kropki i minusa
                clean_str = str(value_str).strip()
                clean_str = re.sub(r'[^\d.,\-]', '', clean_str)

                # Jeśli jest przecinek i kropka, to przecinek to separator tysięcy
                if ',' in clean_str and '.' in clean_str:
                    clean_str = clean_str.replace(',', '')
                # Jeśli jest tylko przecinek, to może być separator dziesiętny
                elif ',' in clean_str and '.' not in clean_str:
                    # Sprawdź czy po przecinku są maksymalnie 2 cyfry (separator dziesiętny)
                    parts = clean_str.split(',')
                    if len(parts) == 2 and len(parts[1]) <= 2:
                        clean_str = clean_str.replace(',', '.')
                    else:
                        clean_str = clean_str.replace(',', '')

                return float(clean_str)
            except (ValueError, AttributeError):
                return None

        # Rentowność netto
        zysk_netto = to_number(obecny.get('zysk_netto'))
        przychody = to_number(obecny.get('przychody_sprzedazy'))

        if zysk_netto is not None and przychody is not None and przychody > 0:
            rentownosc = (zysk_netto / przychody) * 100
            wskazniki['rentownosc_netto'] = f"{rentownosc:.2f}%"

        # Rentowność operacyjna
        zysk_operacyjny = to_number(obecny.get('zysk_operacyjny'))
        if zysk_operacyjny is not None and przychody is not None and przychody > 0:
            rentownosc_op = (zysk_operacyjny / przychody) * 100
            wskazniki['rentownosc_operacyjna'] = f"{rentownosc_op:.2f}%"

        # Marża brutto
        zysk_brutto = to_number(obecny.get('zysk_brutto_sprzedazy'))
        if zysk_brutto is not None and przychody is not None and przychody > 0:
            marza_brutto = (zysk_brutto / przychody) * 100
            wskazniki['marza_brutto'] = f"{marza_brutto:.2f}%"

        # Płynność bieżąca
        aktywa_obrotowe = to_number(obecny.get('aktywa_obrotowe'))
        zobowiazania_kr = to_number(obecny.get('zobowiazania_krotkotermindowe'))

        if aktywa_obrotowe is not None and zobowiazania_kr is not None and zobowiazania_kr > 0:
            plynnosc = aktywa_obrotowe / zobowiazania_kr
            wskazniki['plynnosc_biezaca'] = f"{plynnosc:.2f}"

        # Wskaźnik zadłużenia
        zobowiazania = to_number(obecny.get('zobowiazania_razem'))
        aktywa = to_number(obecny.get('aktywa_razem'))

        if zobowiazania is not None and aktywa is not None and aktywa > 0:
            zadluzenie = (zobowiazania / aktywa) * 100
            wskazniki['wskaznik_zadluzenia'] = f"{zadluzenie:.2f}%"

        # ROE (Return on Equity)
        kapital_wlasny = to_number(obecny.get('kapital_wlasny'))
        if zysk_netto is not None and kapital_wlasny is not None and kapital_wlasny > 0:
            roe = (zysk_netto / kapital_wlasny) * 100
            wskazniki['roe'] = f"{roe:.2f}%"

        # ROA (Return on Assets)
        if zysk_netto is not None and aktywa is not None and aktywa > 0:
            roa = (zysk_netto / aktywa) * 100
            wskazniki['roa'] = f"{roa:.2f}%"

        # === PORÓWNANIA ROK DO ROKU ===

        # Dynamika przychodów (r/r)
        przychody_poprz = to_number(poprzedni.get('przychody_sprzedazy'))
        if przychody is not None and przychody_poprz is not None and przychody_poprz != 0:
            wzrost_przych = ((przychody - przychody_poprz) / abs(przychody_poprz)) * 100
            wskazniki['wzrost_przychodow'] = f"{wzrost_przych:+.2f}%"

        # Dynamika zysku netto (r/r)
        zysk_netto_poprz = to_number(poprzedni.get('zysk_netto'))
        if zysk_netto is not None and zysk_netto_poprz is not None and zysk_netto_poprz != 0:
            wzrost_zysku = ((zysk_netto - zysk_netto_poprz) / abs(
                zysk_netto_poprz)) * 100
            wskazniki['wzrost_zysku_netto'] = f"{wzrost_zysku:+.2f}%"

        # Dynamika zysku operacyjnego (r/r)
        zysk_op_poprz = to_number(poprzedni.get('zysk_operacyjny'))
        if zysk_operacyjny is not None and zysk_op_poprz is not None and zysk_op_poprz != 0:
            wzrost_zysku_op = ((zysk_operacyjny - zysk_op_poprz) / abs(
                zysk_op_poprz)) * 100
            wskazniki['wzrost_zysku_operacyjnego'] = f"{wzrost_zysku_op:+.2f}%"

        # Dynamika aktywów (r/r)
        aktywa_poprz = to_number(poprzedni.get('aktywa_razem'))
        if aktywa is not None and aktywa_poprz is not None and aktywa_poprz > 0:
            wzrost_aktywow = ((aktywa - aktywa_poprz) / aktywa_poprz) * 100
            wskazniki['wzrost_aktywow'] = f"{wzrost_aktywow:+.2f}%"

        # Dynamika kapitału własnego (r/r)
        kapital_poprz = to_number(poprzedni.get('kapital_wlasny'))
        if kapital_wlasny is not None and kapital_poprz is not None and kapital_poprz > 0:
            wzrost_kapitalu = ((kapital_wlasny - kapital_poprz) / kapital_poprz) * 100
            wskazniki['wzrost_kapitalu_wlasnego'] = f"{wzrost_kapitalu:+.2f}%"

        # === PORÓWNANIE MARŻ ===

        # Porównanie marży netto r/r
        if (zysk_netto is not None and przychody is not None and przychody > 0 and
                zysk_netto_poprz is not None and przychody_poprz is not None and przychody_poprz > 0):
            marza_obecna = (zysk_netto / przychody) * 100
            marza_poprz = (zysk_netto_poprz / przychody_poprz) * 100
            zmiana_marzy = marza_obecna - marza_poprz
            wskazniki['zmiana_marzy_netto'] = f"{zmiana_marzy:+.2f} p.p."

    except Exception as e:
        wskazniki['error'] = f"Błąd obliczania wskaźników: {str(e)}"

    return wskazniki

def ekstraktuj_dodatkowe_info(text: str) -> str:
    """Ekstraktuje dodatkowe informacje kontekstowe."""
    info_sections = []

    # Szukaj sekcji z komentarzem zarządu
    m = re.search(r'Komentarz\s+zarządu[:\-]?\s*(.+?)(?=\n\n|\nPodpisy|\nWarszawa|$)',
                  text, re.S | re.I)
    if m:
        info_sections.append(f"Komentarz zarządu: {m.group(1).strip()}")

    # Szukaj informacji o perspektywach
    m = re.search(
        r'(?:Perspektywy|Prognozy|Outlook)[:\-]?\s*(.+?)(?=\n\n|\nPodpisy|\nWarszawa|$)',
        text, re.S | re.I)
    if m:
        info_sections.append(f"Perspektywy: {m.group(1).strip()}")

    # Szukaj informacji o istotnych zdarzeniach
    m = re.search(
        r'(?:Istotne\s+zdarzenia|Wydarzenia)[:\-]?\s*(.+?)(?=\n\n|\nPodpisy|\nWarszawa|$)',
        text, re.S | re.I)
    if m:
        info_sections.append(f"Istotne zdarzenia: {m.group(1).strip()}")

    return '\n\n'.join(info_sections)


def przygotuj_dla_chatgpt_api(dane_raportu: Dict) -> str:
    """
    Formatuje dane raportu do analizy przez ChatGPT API.

    Args:
        dane_raportu: dane zwrócone przez pobierz_raport_finansowy_espi()

    Returns:
        str: sformatowany tekst gotowy do wysłania do ChatGPT
    """
    prompt = f"""Proszę przeanalizuj poniższy raport finansowy polskiej spółki:

=== PODSTAWOWE INFORMACJE ===
• Spółka: {dane_raportu.get('spolka', 'Nie określono')}
• Typ raportu: {dane_raportu.get('typ_raportu', 'Nie określono')}
• Okres sprawozdawczy: {dane_raportu.get('okres', 'Nie określono')}
• Tytuł raportu: {dane_raportu.get('temat', 'Nie określono')}

=== DANE FINANSOWE ==="""

    dane_fin = dane_raportu.get('dane_finansowe', {})
    obecny = dane_fin.get('obecny_okres', {})
    poprzedni = dane_fin.get('poprzedni_okres', {})

    if obecny or poprzedni:
        prompt += "\n\nPORÓWNANIE OKRES BIEŻĄCY vs POPRZEDNI (w tys. zł):\n"

        # Lista kluczowych pozycji do wyświetlenia
        kluczowe_pozycje = [
            ('przychody_sprzedazy', 'Przychody ze sprzedaży'),
            ('koszt_sprzedazy', 'Koszt sprzedaży'),
            ('zysk_brutto_sprzedazy', 'Zysk brutto ze sprzedaży'),
            ('zysk_operacyjny', 'Zysk operacyjny'),
            ('zysk_netto', 'Zysk netto'),
            ('ebitda', 'EBITDA'),
            ('aktywa_razem', 'Aktywa razem'),
            ('aktywa_trwale', 'Aktywa trwałe'),
            ('aktywa_obrotowe', 'Aktywa obrotowe'),
            ('kapital_wlasny', 'Kapitał własny'),
            ('zobowiazania_razem', 'Zobowiązania razem'),
            ('zobowiazania_dlugoterminowe', 'Zobowiązania długoterminowe'),
            ('zobowiazania_krotkotermindowe', 'Zobowiązania krótkoterminowe')
        ]

        for klucz, nazwa in kluczowe_pozycje:
            obecna_wartosc = obecny.get(klucz, '-')
            poprzednia_wartosc = poprzedni.get(klucz, '-')

            if obecna_wartosc != '-' or poprzednia_wartosc != '-':
                prompt += f"\n• {nazwa:<30} | Bieżący: {obecna_wartosc:<15} | Poprzedni: {poprzednia_wartosc}"

    # Dodaj wskaźniki finansowe jeśli dostępne
    wskazniki = dane_raportu.get('wskazniki_finansowe', {})
    if wskazniki:
        prompt += "\n\n=== KLUCZOWE WSKAŹNIKI ===\n"
        wskazniki_nazwy = {
            'rentownosc_netto': 'Rentowność netto',
            'plynnosc_biezaca': 'Płynność bieżąca',
            'wskaznik_zadluzenia': 'Wskaźnik zadłużenia',
            'wzrost_przychodow': 'Wzrost przychodów (r/r)',
            'wzrost_zysku_netto': 'Wzrost zysku netto (r/r)'
        }

        for klucz, nazwa in wskazniki_nazwy.items():
            if klucz in wskazniki:
                prompt += f"• {nazwa}: {wskazniki[klucz]}\n"

    # Dodaj tabele finansowe jeśli dostępne
    tabele = dane_fin.get('tabele_finansowe', [])
    if tabele:
        prompt += "\n=== SZCZEGÓŁOWE TABELE FINANSOWE ===\n"
        for i, tabela in enumerate(tabele[:2], 1):  # Maksymalnie 2 tabele
            prompt += f"\nTabela {i}:\n{tabela[:1000]}...\n"  # Ogranicz długość

    # Główna treść raportu (skrócona)
    tresc = dane_raportu.get('tresc', '')
    if tresc:
        prompt += f"\n=== WYBRANE FRAGMENTY RAPORTU ===\n{tresc[:2000]}...\n"

    # Dodatkowe informacje
    dodatkowe = dane_raportu.get('dodatkowe_info', '')
    if dodatkowe:
        prompt += f"\n=== DODATKOWE INFORMACJE ===\n{dodatkowe}\n"

    prompt += """

=== ZADANIE ANALIZY ===
Na podstawie powyższych danych finansowych, wykonaj szczegółową analizę:

1. **ANALIZA WYNIKÓW FINANSOWYCH**
   - Ocena dynamiki przychodów, kosztów i rentowności
   - Analiza głównych pozycji rachunku zysków i strat
   - Identyfikacja kluczowych trendów

2. **ANALIZA BILANSOWA**
   - Struktura aktywów i pasywów
   - Ocena płynności finansowej
   - Analiza zadłużenia i struktury kapitału

3. **PORÓWNANIE ROK DO ROKU**
   - Analiza zmian w kluczowych pozycjach
   - Ocena dynamiki wzrostu/spadku
   - Identyfikacja przyczyn głównych zmian

4. **WSKAŹNIKI FINANSOWE**
   - Oblicz i oceń kluczowe wskaźniki rentowności, płynności i zadłużenia
   - Porównaj z poprzednim okresem
   - Oceń kondycję finansową spółki

5. **PODSUMOWANIE I REKOMENDACJE**
   - Ogólna ocena sytuacji finansowej spółki
   - Główne zagrożenia i możliwości
   - Rekomendacje dla inwestorów (pozytywne/negatywne/neutralne)

Zwróć szczególną uwagę na:
- Zmiany w marżach i rentowności
- Trendy w przepływach pieniężnych
- Zmiany w strukturze kosztów
- Poziom zadłużenia i płynność
- Jakość wzrostu (jeśli występuje)

Przedstaw analizę w sposób jasny i zrozumiały, z konkretnymi liczbami i procentami zmian."""

    return prompt