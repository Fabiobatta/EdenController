"""
Core/I18n.py
Translations for every string the launcher shows.

    from Core.I18n import t, set_language
    set_language("auto")          # "it", "en" or "auto" (follow the OS)
    t("slot_empty")               # -> "PREMI Ⓐ PER GIOCARE"
    t("footer_launch", name="Eden")

Emulator packages add their own strings with register(); a key missing in
the current language falls back to English, then to the key itself.
"""

import locale
import sys

DEFAULT_LANGUAGE = "en"

STRINGS = {
    "en": {
        # Player grid
        "title_players":        "CONTROLLER SETUP",
        "slot_empty":           "PRESS Ⓐ TO JOIN",
        "slot_hint":            "Ⓑ LEAVE   |   Ⓧ PROFILE",
        "slot_profile":         "◄   Profile: {name}   ►",
        "slot_profile_hint":    "Ⓐ CONFIRM   |   Ⓑ CANCEL",
        # Footer
        "footer_launch_game":   "☰ LAUNCH GAME",
        "footer_launch":        "☰ LAUNCH {name}",
        "footer_choose":        "☰ CHOOSE GAME",
        "footer_quit":          "⧉ QUIT",
        "footer_play":          "Ⓐ PLAY",
        "footer_back":          "Ⓑ BACK",
        # Game list
        "title_games":          "CHOOSE A GAME",
        "games_position":       "{index} / {total}     ◄ LB  page  RB ►",
        # Alerts
        "alert_no_pads_title":  "⚠️ NO CONTROLLERS",
        "alert_no_pads_text":   "{name} will keep its current input settings.",
        "alert_continue":       "Ⓐ CONTINUE",
        "alert_back":           "Ⓑ BACK",
        "alert_exit_title":     "EXIT LAUNCHER?",
        "alert_exit_text":      "Are you sure you want to quit?",
        "alert_yes":            "Ⓐ YES",
        "alert_no":             "Ⓑ NO",
        "alert_kill_title":     "CLOSE GAME?",
        "alert_kill_text":      "How would you like to proceed?",
        "alert_kill_launcher":  "Ⓐ LAUNCHER",
        "alert_kill_desktop":   "Ⓨ DESKTOP",
        "alert_kill_cancel":    "Ⓑ CANCEL",
        # Toasts
        "toast_disconnected":   "⚠️ {name} disconnected!",
        # Error dialogs
        "error_launch_title":   "Launch Error",
        "error_launch_text":    "Failed to start {name}.\n{error}",
        "error_missing_title":  "Missing File",
        "error_missing_text":   "Could not find {path}",
    },
    "it": {
        "title_players":        "ASSEGNA I CONTROLLER",
        "slot_empty":           "PREMI Ⓐ PER GIOCARE",
        "slot_hint":            "Ⓑ ESCI   |   Ⓧ PROFILO",
        "slot_profile":         "◄   Profilo: {name}   ►",
        "slot_profile_hint":    "Ⓐ CONFERMA   |   Ⓑ ANNULLA",
        "footer_launch_game":   "☰ AVVIA GIOCO",
        "footer_launch":        "☰ AVVIA {name}",
        "footer_choose":        "☰ SCEGLI GIOCO",
        "footer_quit":          "⧉ ESCI",
        "footer_play":          "Ⓐ GIOCA",
        "footer_back":          "Ⓑ INDIETRO",
        "title_games":          "SCEGLI UN GIOCO",
        "games_position":       "{index} / {total}     ◄ LB  pagina  RB ►",
        "alert_no_pads_title":  "⚠️ NESSUN CONTROLLER",
        "alert_no_pads_text":   "{name} userà le impostazioni dei controlli attuali.",
        "alert_continue":       "Ⓐ CONTINUA",
        "alert_back":           "Ⓑ INDIETRO",
        "alert_exit_title":     "USCIRE DAL LAUNCHER?",
        "alert_exit_text":      "Vuoi davvero uscire?",
        "alert_yes":            "Ⓐ SÌ",
        "alert_no":             "Ⓑ NO",
        "alert_kill_title":     "CHIUDERE IL GIOCO?",
        "alert_kill_text":      "Cosa vuoi fare?",
        "alert_kill_launcher":  "Ⓐ LAUNCHER",
        "alert_kill_desktop":   "Ⓨ DESKTOP",
        "alert_kill_cancel":    "Ⓑ ANNULLA",
        "toast_disconnected":   "⚠️ {name} scollegato!",
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
