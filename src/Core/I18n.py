"""
Core/I18n.py
Translations for every string the launcher shows.

    from Core.I18n import t, set_language
    set_language("auto")          # "it", "en" or "auto" (follow the OS)
    t("title_players")            # -> "Chi gioca?"
    t("hint_launch", name="Eden")

Strings may embed controller buttons as "[a]", "[b]", "[lb]", "[start]",
"[dpad]"...: the UI draws them as button pictures (Core/Glyphs.py).

Emulator packages add their own strings with register(); a key missing in
the current language falls back to English, then to the key itself.
"""

import datetime
import locale
import sys
import time

DEFAULT_LANGUAGE = "en"

STRINGS = {
    "en": {
        # Player grid
        "title_players":        "Who's playing?",
        "subtitle_players":     "Press [a] on your controller to join",
        "slot_empty":           "Press [a]",
        "slot_profile":         "Layout: {name}",
        "slot_hints":           "[x] Layout    [b] Leave",
        "slot_editing_hints":   "[dpad] Change    [a] OK    [b] Cancel",
        "tip_kill":             "During a game, press {combo} to close {name}",
        # Footer hints
        "hint_join":            "Join",
        "hint_choose":          "Choose game",
        "hint_launch_game":     "Play",
        "hint_launch":          "Start {name}",
        "hint_quit":            "Quit",
        "hint_browse":          "Browse",
        "hint_page":            "Page",
        "hint_play":            "Play",
        "hint_back":            "Back",
        "hint_filter_on":       "Only {n}+ players",
        "hint_filter_off":      "All games",
        # Game grid
        "games_loading":        "Loading games…",
        "players_one":          "1 player",
        "players_range":        "1–{n} players",
        "players_fit":          "Good for {n} players",
        "players_too_many":     "Max {max} · you are {n}",
        "games_filter":         "Filter: {n}+ players",
        "games_none_for":       "No game supports {n} players",
        # Alerts
        "alert_no_pads_title":  "NO CONTROLLERS",
        "alert_no_pads_text":   "{name} will keep its current input settings.",
        "alert_continue":       "Continue",
        "alert_back":           "Back",
        "alert_exit_title":     "EXIT LAUNCHER?",
        "alert_exit_text":      "Are you sure you want to quit?",
        "alert_yes":            "Yes",
        "alert_no":             "No",
        "alert_kill_title":     "CLOSE GAME?",
        "alert_kill_text":      "How would you like to proceed?",
        "alert_kill_launcher":  "Launcher",
        "alert_kill_desktop":   "Desktop",
        "alert_kill_cancel":    "Cancel",
        # Toasts
        "hint_surprise":        "Surprise me",
        "hint_sort":            "Sort: {mode}",
        "hint_favorite":        "Favourite",
        "hint_resume":          "Resume {title}",
        "sort_recent":          "Recent",
        "sort_az":              "A–Z",
        "sort_most":            "Most played",
        "toast_favorite_on":    "Added to favourites",
        "toast_favorite_off":   "Removed from favourites",
        # Play time
        "time_hours":           "{h} h {m} min",
        "time_minutes":         "{m} min",
        "last_today":           "today",
        "last_yesterday":       "yesterday",
        "last_days":            "{n} days ago",
        # Roulette
        "roulette_title":       "Tonight we play…",
        "roulette_pool":        "Drawing from {n} games for {p} players",
        "roulette_pool_all":    "Drawing from {n} games",
        "roulette_hints":       "[a] Play      [x] Spin again      [b] Back",
        "roulette_none":        "No game for {n} players",
        # Achievements
        "hint_trophies":        "Achievements",
        "trophy_unlocked":      "ACHIEVEMENT UNLOCKED",
        "trophies_title":       "Achievements",
        "trophies_count":       "{n} of {total} unlocked",
        "trophies_close":       "[b] Close",
        "ach_first_game":       "Here we go",
        "ach_first_game_desc":  "Start your first game from the launcher",
        "ach_party2":           "Company",
        "ach_party2_desc":      "Play with 2 or more players",
        "ach_party4":           "Party of four",
        "ach_party4_desc":      "Play with 4 or more players",
        "ach_party8":           "All aboard",
        "ach_party8_desc":      "Play with 8 players",
        "ach_roulette":         "Let fate decide",
        "ach_roulette_desc":    "Play a game picked by the roulette",
        "ach_destiny":          "Destiny",
        "ach_destiny_desc":     "The roulette picks the same game 3 times",
        "ach_variety":          "Mixed evening",
        "ach_variety_desc":     "Play 3 different games without closing the launcher",
        "ach_collector":        "Collector",
        "ach_collector_desc":   "Play 10 different games",
        "ach_favorites":        "Favourites",
        "ach_favorites_desc":   "Add 5 games to your favourites",
        "ach_marathon":         "Marathon",
        "ach_marathon_desc":    "Play for 3 hours in a row",
        "ach_devoted":          "Devoted",
        "ach_devoted_desc":     "10 hours on the same game",
        "ach_veteran":          "Veteran",
        "ach_veteran_desc":     "50 hours on the same game",
        "ach_century":          "Centenary",
        "ach_century_desc":     "100 hours of play in total",
        "ach_night_owl":        "Night owl",
        "ach_night_owl_desc":   "Play after 2 in the morning",
        "toast_disconnected":   "{name} disconnected",
        # Error dialogs
        "error_launch_title":   "Launch Error",
        "error_launch_text":    "Failed to start {name}.\n{error}",
        "error_missing_title":  "Missing File",
        "error_missing_text":   "Could not find {path}",
    },
    "it": {
        "title_players":        "Chi gioca?",
        "subtitle_players":     "Premi [a] sul tuo controller per unirti",
        "slot_empty":           "Premi [a]",
        "slot_profile":         "Layout: {name}",
        "slot_hints":           "[x] Layout    [b] Esci",
        "slot_editing_hints":   "[dpad] Cambia    [a] OK    [b] Annulla",
        "tip_kill":             "Durante il gioco premi {combo} per chiudere {name}",
        "hint_join":            "Unisciti",
        "hint_choose":          "Scegli gioco",
        "hint_launch_game":     "Gioca",
        "hint_launch":          "Avvia {name}",
        "hint_quit":            "Esci",
        "hint_browse":          "Sfoglia",
        "hint_page":            "Pagina",
        "hint_play":            "Gioca",
        "hint_back":            "Indietro",
        "hint_filter_on":       "Solo per {n}+ giocatori",
        "hint_filter_off":      "Tutti i giochi",
        "games_loading":        "Caricamento giochi…",
        "players_one":          "1 giocatore",
        "players_range":        "1–{n} giocatori",
        "players_fit":          "Va bene per {n} giocatori",
        "players_too_many":     "Max {max} · siete in {n}",
        "games_filter":         "Filtro: {n}+ giocatori",
        "games_none_for":       "Nessun gioco supporta {n} giocatori",
        "alert_no_pads_title":  "NESSUN CONTROLLER",
        "alert_no_pads_text":   "{name} userà le impostazioni dei controlli attuali.",
        "alert_continue":       "Continua",
        "alert_back":           "Indietro",
        "alert_exit_title":     "USCIRE DAL LAUNCHER?",
        "alert_exit_text":      "Vuoi davvero uscire?",
        "alert_yes":            "Sì",
        "alert_no":             "No",
        "alert_kill_title":     "CHIUDERE IL GIOCO?",
        "alert_kill_text":      "Cosa vuoi fare?",
        "alert_kill_launcher":  "Launcher",
        "alert_kill_desktop":   "Desktop",
        "alert_kill_cancel":    "Annulla",
        "hint_surprise":        "Sorpresa",
        "hint_sort":            "Ordina: {mode}",
        "hint_favorite":        "Preferito",
        "hint_resume":          "Riprendi {title}",
        "sort_recent":          "Recenti",
        "sort_az":              "A–Z",
        "sort_most":            "Più giocati",
        "toast_favorite_on":    "Aggiunto ai preferiti",
        "toast_favorite_off":   "Tolto dai preferiti",
        "time_hours":           "{h} h {m} min",
        "time_minutes":         "{m} min",
        "last_today":           "oggi",
        "last_yesterday":       "ieri",
        "last_days":            "{n} giorni fa",
        "roulette_title":       "Stasera si gioca a…",
        "roulette_pool":        "Estrazione tra {n} giochi per {p} giocatori",
        "roulette_pool_all":    "Estrazione tra {n} giochi",
        "roulette_hints":       "[a] Gioca      [x] Ritira      [b] Indietro",
        "roulette_none":        "Nessun gioco per {n} giocatori",
        "hint_trophies":        "Traguardi",
        "trophy_unlocked":      "TRAGUARDO SBLOCCATO",
        "trophies_title":       "Traguardi",
        "trophies_count":       "{n} su {total} sbloccati",
        "trophies_close":       "[b] Chiudi",
        "ach_first_game":       "Si comincia",
        "ach_first_game_desc":  "Avvia il primo gioco dal launcher",
        "ach_party2":           "In compagnia",
        "ach_party2_desc":      "Gioca in 2 o più",
        "ach_party4":           "Serata in 4",
        "ach_party4_desc":      "Gioca in 4 o più",
        "ach_party8":           "Tutti a bordo",
        "ach_party8_desc":      "Gioca in 8",
        "ach_roulette":         "Che la sorte decida",
        "ach_roulette_desc":    "Gioca a un gioco scelto dalla roulette",
        "ach_destiny":          "Destino",
        "ach_destiny_desc":     "La roulette sceglie lo stesso gioco 3 volte",
        "ach_variety":          "Serata varia",
        "ach_variety_desc":     "Gioca 3 giochi diversi senza chiudere il launcher",
        "ach_collector":        "Collezionista",
        "ach_collector_desc":   "Gioca 10 giochi diversi",
        "ach_favorites":        "I miei preferiti",
        "ach_favorites_desc":   "Aggiungi 5 giochi ai preferiti",
        "ach_marathon":         "Maratona",
        "ach_marathon_desc":    "Gioca 3 ore di fila",
        "ach_devoted":          "Appassionato",
        "ach_devoted_desc":     "10 ore sullo stesso gioco",
        "ach_veteran":          "Veterano",
        "ach_veteran_desc":     "50 ore sullo stesso gioco",
        "ach_century":          "Centenario",
        "ach_century_desc":     "100 ore di gioco in totale",
        "ach_night_owl":        "Nottambulo",
        "ach_night_owl_desc":   "Gioca dopo le 2 di notte",
        "toast_disconnected":   "{name} scollegato",
        "error_launch_title":   "Errore di avvio",
        "error_launch_text":    "Impossibile avviare {name}.\n{error}",
        "error_missing_title":  "File mancante",
        "error_missing_text":   "Impossibile trovare {path}",
    },
}

_language = DEFAULT_LANGUAGE


def register(strings):
    """Add strings: {"en": {key: text}, "it": {key: text}}."""
    for language, table in strings.items():
        STRINGS.setdefault(language, {}).update(table)


def detect_language():
    """Two-letter code of the OS user interface language."""
    try:
        if sys.platform == "win32":
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # Primary language ids: https://learn.microsoft.com/windows/win32/intl/language-identifier-constants-and-strings
            return {0x10: "it", 0x09: "en"}.get(langid & 0x3FF, DEFAULT_LANGUAGE)
        code = locale.getlocale()[0] or ""
        return code[:2].lower() or DEFAULT_LANGUAGE
    except Exception:
        return DEFAULT_LANGUAGE


def set_language(language):
    """Select the UI language; unknown languages fall back to English."""
    global _language
    language = (language or "auto").strip().lower()
    if language == "auto":
        language = detect_language()
    _language = language if language in STRINGS else DEFAULT_LANGUAGE
    return _language


def get_language():
    return _language


def t(key, **values):
    text = STRINGS.get(_language, {}).get(key) or STRINGS[DEFAULT_LANGUAGE].get(key) or key
    return text.format(**values) if values else text


def players_label(count):
    """ "1 giocatore" / "1–4 giocatori" for a game's max player count."""
    return t("players_one") if count <= 1 else t("players_range", n=count)


def playtime_label(seconds):
    """ "12 h 5 min" / "40 min"."""
    minutes = int(seconds) // 60
    if minutes >= 60:
        return t("time_hours", h=minutes // 60, m=minutes % 60)
    return t("time_minutes", m=minutes)


def last_played_label(epoch, now=None):
    """ "oggi" / "ieri" / "3 giorni fa" / "12/05/2026"."""
    today = datetime.date.fromtimestamp(time.time() if now is None else now)
    days = (today - datetime.date.fromtimestamp(epoch)).days
    if days <= 0:
        return t("last_today")
    if days == 1:
        return t("last_yesterday")
    if days < 30:
        return t("last_days", n=days)
    return time.strftime("%d/%m/%Y", time.localtime(epoch))
