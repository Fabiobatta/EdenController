# <img src="assets/EdenLauncherPNG.png" alt="Eden Launcher" width="28"> Eden Launcher

**Assegna i controller ai giocatori di [Eden](https://eden-emu.dev) usando solo il controller, subito prima di avviare il gioco.**

Pensato per il game streaming con **Vibeshine / Sunshine / Apollo** come host e
**Moonlight** (per esempio Moonlight per Xbox) come client, ma funziona anche in
locale (HTPC, couch gaming).

È la versione per Eden di [RyujinxLauncher](https://github.com/Artomos-dev/RyujinxLauncher)
di Artomos: il motore (`src/Core/`) è il suo, l'adapter `src/Eden/` è specifico per Eden.

---

## Il problema

Eden (come yuzu) salva ogni tasto di ogni giocatore in `qt-config.ini`, e
identifica il controller fisico con due campi:

```
player_0_button_a="button:1,engine:sdl,guid:030000005e0400008e02000000007801,port:0"
```

* `guid` – il tipo di controller (vendor/product/driver SDL)
* `port` – *l'ordine di connessione* tra i controller con lo stesso GUID

Con lo streaming, l'host crea un **Xbox 360 Controller virtuale identico** per
ogni pad collegato al client Moonlight. Tutti hanno lo stesso GUID, quindi
l'unica cosa che distingue il Giocatore 1 dal Giocatore 2 è `port`, cioè
l'ordine in cui i pad si sono collegati alla sessione. Basta che un controller
si accenda prima di un altro e i giocatori risultano scambiati, oppure un
controller non risponde, e per sistemarlo bisogna aprire le impostazioni di Eden
con mouse e tastiera.

## La soluzione

Configuri Eden in qualunque modo, ma lo avvii tramite `EdenLauncher`:

1. si apre una schermata a tutto schermo controllabile con il gamepad;
2. ogni persona preme **Ⓐ** sul proprio controller: il primo che preme è il
   Giocatore 1, il secondo il Giocatore 2, e così via (fino a 8). Il controller
   **vibra** una volta per il Giocatore 1, due per il Giocatore 2, ecc.;
3. si preme **☰ (Start)**: se il launcher è stato avviato senza un gioco si apre
   la **lista dei giochi**, altrimenti parte subito il gioco passato da Vibeshine;
4. il launcher riscrive *solo* le chiavi `player_N_*` di `qt-config.ini` con
   `guid`/`port` corretti per ogni controller, attiva la **modalità TV** se ci
   sono 2 o più giocatori, e avvia Eden.

Tutto il resto della configurazione (grafica, audio, hotkey...) non viene toccato.
L'interfaccia è in **italiano** (o inglese, in base alla lingua di Windows o a
`EdenLauncher.ini`).

---

## Comandi

| Azione | Tasto (Xbox) |
| :--- | :--- |
| Diventare il giocatore successivo | `Ⓐ` |
| Liberare il proprio slot | `Ⓑ` |
| Scegliere il profilo di mappatura | `Ⓧ`, poi `◄ ►` e `Ⓐ` per confermare |
| Avviare il gioco / aprire la lista giochi | `☰` (Start) |
| Uscire dal launcher | `⧉` (Back) |
| **Chiudere Eden bloccato** | Premi insieme `⧉` + `LB` + `RB` su *qualsiasi* controller (combinazione configurabile) |

Nella **lista giochi**:

| Azione | Tasto (Xbox) |
| :--- | :--- |
| Su / giù (tenere premuto per scorrere) | Croce direzionale o levetta sinistra |
| Pagina precedente / successiva | `LB` / `RB` |
| Avviare il gioco selezionato | `Ⓐ` |
| Tornare ai controller | `Ⓑ` |

Quando chiudi un gioco avviato dalla lista torni alla lista, con gli stessi
giocatori: puoi sceglierne subito un altro. Il launcher parte dall'ultimo gioco
giocato.

Dal menu di chiusura: `Ⓐ` torna al launcher (per riassegnare i controller),
`Ⓨ` esce del tutto, `Ⓑ` annulla e torna al gioco.

---

## Installazione (Windows)

1. Scarica `EdenLauncher.exe` dalla pagina **Releases** del repository
   (la release più recente, sezione *Assets*), oppure compilalo (vedi sotto).
2. Mettilo **nella stessa cartella di `eden.exe`**:

   ```text
   C:\Emulatori\Eden\
       ├── eden.exe
       ├── EdenLauncher.exe   <-- qui
       └── user\              (solo se usi Eden in modalità portable)
   ```

   In alternativa crea accanto al launcher un file `EdenPath.config` che contiene
   solo il percorso della cartella di Eden, per esempio `D:\Emulatori\Eden`.

3. **Configurazione una tantum dentro Eden** (consigliata):
   apri Eden → *Emulazione → Configura → Controlli*, imposta il **Giocatore 1**
   con un controller Xbox (dispositivo SDL, pulsante *Mappatura automatica*),
   regola deadzone, vibrazione ecc. e salva. Il launcher parte da questa
   mappatura per tutti i controller, cambiando solo `guid` e `port`, e di
   default la converte nel **layout Xbox** (vedi *Profili di mappatura*).
   A/B/X/Y li imposta sempre il launcher, quindi non importa come sono mappati
   in Eden.

   Se il Giocatore 1 non ha una mappatura SDL, il launcher usa un layout Xbox
   (XInput) integrato.

Dove il launcher cerca la configurazione (come fa Eden):

| Sistema | `qt-config.ini` |
| :--- | :--- |
| Windows portable | `<cartella di Eden>\user\config\qt-config.ini` |
| Windows | `%APPDATA%\eden\config\qt-config.ini` |
| Linux / macOS | `./user/config/` se esiste, altrimenti `~/.config/eden/qt-config.ini` |

Per casi particolari si può forzare la cartella utente di Eden con la variabile
d'ambiente `EDEN_LAUNCHER_USER_DIR` (quella che contiene `config\`).

I log del launcher finiscono nella cartella `log` di Eden
(`%APPDATA%\eden\log\EdenLauncher_*.log` su Windows).

---

## Vibeshine / Sunshine / Apollo + Moonlight per Xbox

1. Nell'interfaccia web dell'host apri **Applications → Add New**.
2. **Application Name**: "Eden" (una sola app per tutti i giochi) oppure il nome
   di un gioco.
3. **Command**, a scelta:

   * **Una sola app con la lista giochi** (consigliato):

     ```text
     "C:\Emulatori\Eden\EdenLauncher.exe"
     ```

     Dopo aver assegnato i controller, `Start` apre la lista dei giochi presi
     dalle cartelle configurate in Eden.
   * **Un'app per gioco**:

     ```text
     "C:\Emulatori\Eden\EdenLauncher.exe" -f -g "D:\Giochi\Switch\NomeGioco.nsp"
     ```

     Tutti gli argomenti dopo il launcher vengono passati così come sono a Eden
     (`-f` = schermo intero, `-g` = gioco da avviare).
4. **Working Directory**: la cartella di Eden, es. `C:\Emulatori\Eden`.
5. Lato host, nelle impostazioni **Input** lascia l'emulazione gamepad su
   **Xbox 360 (X360)** o *Auto*.
6. Da Moonlight su Xbox: collega/accendi tutti i controller, avvia l'app, e sulla
   schermata del launcher ogni giocatore preme `Ⓐ` nell'ordine voluto, poi `Start`.

Note utili:

* **Più di 4 controller**: XInput su Windows gestisce al massimo 4 pad. Per
  5–8 giocatori imposta in Vibeshine l'emulazione **DualShock 4 (DS4)**.
  Il launcher funziona allo stesso modo (usa i GUID e le port di quei pad).
* Se un gioco ha una **configurazione personalizzata** con un profilo di input
  impostato (`config\custom\<titleid>.ini` → `player_N_profile_name`), quella
  configurazione ha la precedenza su quella globale scritta dal launcher.
  Lascia i controlli dei giochi sulla configurazione globale.
* Se usi Joy-Con o Pro Controller Nintendo con i driver interni di Eden
  ("Enable direct Joy-Con/Pro Controller driver"), quei controller non passano da
  SDL e il launcher non può assegnarli.

---

## Impostazioni (`EdenLauncher.ini`)

Al primo avvio il launcher crea `EdenLauncher.ini` accanto a `EdenLauncher.exe`,
con tutte le opzioni commentate. Si modifica con il Blocco note; le righe che
iniziano con `;` sono commenti.

```ini
[Launcher]
language = auto
rumble = true
kill_combo = back+lb+rb

[Eden]
layout = Xbox
docked = auto
controller_applet = off
game_picker = true
game_dirs =
fullscreen = true
```

| Opzione | Valori | Cosa fa |
| :--- | :--- | :--- |
| `language` | `auto`, `it`, `en` | Lingua dell'interfaccia (`auto` = quella di Windows) |
| `rumble` | `true`, `false` | Vibrazione di conferma quando un controller prende uno slot |
| `kill_combo` | es. `back+lb+rb` | Tasti da premere insieme per chiudere Eden durante il gioco |
| `layout` | `Xbox`, `Nintendo`, nome di un profilo | Layout predefinito dei tasti (vedi *Profili di mappatura*) |
| `docked` | `auto`, `always`, `never` | Modalità TV: con 2+ giocatori, sempre, o non toccarla |
| `controller_applet` | `off`, `on`, `keep` | La finestra "controller" che alcuni giochi aprono: saltata (`off`), mostrata, o lasciata come in Eden |
| `game_picker` | `true`, `false` | Lista giochi quando il launcher parte senza un gioco |
| `game_dirs` | cartelle separate da `;` | Dove cercare i giochi; vuoto = le cartelle configurate in Eden |
| `fullscreen` | `true`, `false` | Avvia a schermo intero i giochi scelti dalla lista |

I commenti vanno su righe proprie: dopo un valore `;` non è un commento, perché
separa le cartelle in `game_dirs`.

* **kill_combo**: tasti disponibili `a b x y back start lb rb ls rs up down left
  right`, uniti da `+` (es. `back+start`). Se scrivi un nome sbagliato si usa
  `back+lb+rb`, così un errore non ti lascia senza modo di chiudere Eden.
* **controller_applet**: alcuni giochi, all'avvio o prima del multigiocatore,
  chiedono di "collegare i controller": Eden apre allora una sua finestra che si
  usa solo con il mouse. Con `off` (predefinito) il launcher attiva l'opzione di
  Eden *Disabilita applet controller*: il gioco prosegue subito con i giocatori
  assegnati nel launcher.
* **docked**: molti giochi accettano più controller solo in modalità TV. Con
  `auto` il launcher la attiva quando ci sono almeno 2 giocatori (non la
  disattiva mai). Una configurazione personalizzata del gioco in Eden che imposta
  una modalità diversa ha comunque la precedenza.
* **game_dirs**: se vuoto, il launcher usa le cartelle dei giochi aggiunte in Eden
  (rispettando l'opzione "scansione sottocartelle"). La lista mostra i file
  `.nsp`, `.xci`, `.nro`; aggiornamenti e DLC con il title ID nel nome
  (`[0100...800]`, `[0100...001]`) vengono nascosti. Il nome mostrato è il nome del
  file ripulito dalle parti tra `[ ]`.

Accanto al file il launcher salva `EdenLauncher.state.json` (l'ultimo gioco
giocato): si può cancellare senza problemi.

---

## Profili di mappatura

Due profili sono integrati, entrambi ricavati dalla mappatura del Giocatore 1:

| Tasto sull'Xbox | **Xbox** (predefinito) | **Nintendo** |
| :--- | :--- | :--- |
| A (in basso) | A di Switch | B di Switch |
| B (a destra) | B di Switch | A di Switch |
| X (a sinistra) | X di Switch | Y di Switch |
| Y (in alto) | Y di Switch | X di Switch |

* **Xbox**: ogni tasto fa quello che c'è scritto sopra. In Mario Kart, per
  esempio, si accelera con l'**A** dell'Xbox (in basso), come indicano i giochi.
* **Nintendo**: stessa *posizione* del Pro Controller, cioè la mappatura
  automatica di Eden (in Mario Kart si accelera col tasto a destra, la B dell'Xbox).

Levette, croce direzionale, dorsali, grilletti, Start e Back sono uguali nei due
profili.

I quattro tasti A/B/X/Y non vengono copiati dal Giocatore 1 ma **impostati a ogni
avvio** leggendo da SDL quale tasto fisico del controller è in basso, a destra, a
sinistra e in alto. Così il risultato è sempre lo stesso, anche se Eden, alla
chiusura, ha salvato in `qt-config.ini` la mappatura scritta dal launcher.

Il launcher elenca anche i profili che salvi in Eden (*Configura → Controlli →
Profilo → Salva*), cioè i file `config\input\*.ini` che usano un controller SDL.
Con `Ⓧ` ogni giocatore può sceglierne uno diverso; la scelta viene ricordata
per quel controller finché resta collegato. Un profilo salvato con il nome
`Xbox` o `Nintendo` sostituisce quello integrato.

---

## Come funziona (dettagli tecnici)

Tutto deriva dal codice sorgente di Eden (`src/input_common/drivers/sdl_driver.cpp`
e `src/frontend_common/config.cpp`):

* **GUID**: Eden usa il GUID SDL del joystick con i byte 2–3 (CRC del nome)
  azzerati, stampato in esadecimale minuscolo.
* **Port**: l'n-esimo joystick con un certo GUID, nell'ordine di enumerazione
  di SDL, ha `port:n`. Il launcher re-inizializza SDL e riesamina i controller
  un istante prima di scrivere il file, così vede lo stesso ordine che vedrà Eden.
* **Stesso driver SDL di Eden**: il driver SDL che gestisce un pad (XInput,
  RawInput, HIDAPI...) fa parte del GUID. Il launcher imposta per la propria
  istanza SDL gli stessi hint di Eden (`SDL_JOYSTICK_RAWINPUT` secondo
  `enable_raw_input`, `SDL_JOYSTICK_HIDAPI_XBOX=0`, driver Joy-Con/Pro, WGI...),
  letti da `qt-config.ini`. Questi hint non vengono passati al processo di Eden.
* **SDL3**: le versioni attuali di Eden usano SDL3 (le prime release del 2025
  usavano SDL2). Il launcher usa la libreria SDL presente accanto a Eden se c'è,
  altrimenti quella inclusa nell'eseguibile (la build Windows della CI include
  `SDL3.dll`). Si può forzare con `EDEN_LAUNCHER_SDL=SDL2` o `SDL3`.
* **Scrittura sicura**: `qt-config.ini` viene modificato riga per riga (nessun
  riordino, BOM e fine riga CRLF preservati) e salvato in modo atomico. Per ogni
  giocatore vengono scritte `player_N_connected`, `player_N_type` (Pro Controller)
  e le 26 chiavi di mappatura (con `\default=false`); i giocatori non assegnati
  vengono disconnessi. Se non assegni nessun controller il file non viene toccato.

---

## Compilare da sorgente

Prerequisiti: Python 3.10+ (su Linux anche `python3-venv` e `python3-tk`).

```bat
build.bat Eden
```

```bash
./build.sh Eden
```

Lo script crea `.venv`, installa `requirements.txt` e produce
`dist\EdenLauncher.exe` (o `dist/EdenLauncher`). Se nella cartella `sdl\` metti
`SDL3.dll` (scaricabile dalle [release di SDL](https://github.com/libsdl-org/SDL/releases),
file `SDL3-<versione>-win32-x64.zip`), viene incluso nell'eseguibile.

Test:

```bash
python -m unittest discover -s tests -v
```

Struttura:

```text
src/
├── EdenLauncher.py      # entry point
├── Core/                # motore generico (UI, SDL, hot-plug, kill combo, lista giochi,
│                        #   impostazioni, traduzioni) - derivato da RyujinxLauncher
└── Eden/
    ├── Eden.py          # percorsi, backend SDL, hint SDL identici a Eden
    ├── Config.py        # GUID/port, layout Xbox/Nintendo, modalità TV, scrittura di qt-config.ini
    ├── Games.py         # cartelle dei giochi di Eden, ricerca e nomi dei giochi
    └── Ini.py           # editor INI che non altera il resto del file
tests/test_eden.py
```

---

## Licenza e crediti

Il motore in `src/Core/` e gli script di build derivano da
[RyujinxLauncher](https://github.com/Artomos-dev/RyujinxLauncher) di **Artomos**,
distribuito con licenza **CC BY-NC 4.0**. Questo progetto è distribuito con la
stessa licenza: uso **non commerciale**, con attribuzione. Vedi [LICENSE.md](LICENSE.md).

Non è affiliato al progetto Eden, a Ryujinx né a Nintendo. Usalo solo con
software ottenuto legalmente.
