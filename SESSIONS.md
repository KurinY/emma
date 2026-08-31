# Session Log

Tracks what each Claude Code session did, what was left pending, and notes
for the next session. Newest entry at the top.

---

## 2026-09-01 — Session 7

**Status:** Completa in locale; **non ancora pushata ne' deployata**

**Context:** Continuation di Session 6. Mandato aperto dell'utente: *"continuare
a lavorare sulla versione corrente e apportare tutte le modifiche di
miglioramento che ti vengono in mente."* Ho cercato difetti misurando, non
immaginando: copertura, sonde sui casi limite, e ispezione di cio' che gira
davvero sul server.

**Difetti trovati e corretti:**
- **`TelegramAdapter.stop()` concatenava tre passi**, quindi il primo che
  sollevava saltava gli altri due e lasciava la sessione HTTP e la task queue
  di PTB rilasciate a meta'. **Lo stesso difetto del lifespan**, un livello piu'
  in basso, e piu' probabile li': su questo host una connessione su venti a
  Telegram fallisce, e lo spegnimento e' proprio quando cade. Ne' `start()` ne'
  `stop()` avevano un test: ora `adapters/telegram.py` e' al 100%.
- **Il bot puo' diventare sordo senza morire.** Se il long polling si ferma
  mentre il processo vive, nessuno la raggiunge piu' e `/health` rispondeva
  `ok`. E' il sintomo che l'utente ha segnalato due volte il 31 agosto. Ora
  `/health` riporta `telegram: listening|not polling` e va in `503` — e siccome
  `backup.sh` lo interroga ogni notte, il guasto viene trovato da un job che
  gira comunque.
- **Lo stamp `VERSION` poteva mentire in silenzio.** Copiare un file con `scp` e
  riavviare lascia lo stamp a dichiarare un commit che non e' piu' quello che
  gira — **errore che ho commesso io due volte** il 31 agosto, un'ora dopo aver
  scritto che lo stamp aveva reso la domanda rispondibile. All'avvio ora si
  confronta l'installazione con l'ora del deploy e si segnala cosa e' cambiato.
- **Ogni manifest di backup diceva `git commit: not a git repository`.** Vero e
  inutile: un archivio ripristinato fra sei mesi non era legabile a una
  versione. Ora, quando non c'e' un checkout, legge lo stamp `VERSION`.
- **Un pacchetto di provider mancante produceva un traceback** invece della riga
  che dice cosa fare. Ora `MissingDependencyError`, riportato come un `.env`
  sbagliato: exit 2, nessuna traccia di stack.
- **`_describe_age` diceva "meno di un minuto fa" di 89 secondi.** Testo che
  leggi tu, passato da un modello che non puo' verificarlo. Corretta la soglia;
  emersi cosi' tre singolari mai gestiti ("1 minuti fa").
- **`.gitattributes` esteso a tutti i file di testo.** Il working copy era meta'
  CRLF e meta' LF, il che fa fallire in silenzio una modifica byte-esatta su un
  file e riuscire sull'altro: mi e' costato una diagnosi sbagliata stanotte.
  `git add --renormalize` non ha mosso un solo contenuto.

**Verificato sul server, non supposto:**
- Il timer del backup gira (ultimo esito `0`), gli archivi esistono in
  `/var/backups/emma` a `0600`, e **il fallback sul disco primario funziona**
  (`/mnt/backup/emma` non esiste). Disco al 27%.
- Un archivio reale si apre, contiene lo snapshot del DB e `.env`, ed esclude
  `.venv`, le cache e `data/`.

**Non fatto, deliberatamente:** dividere `core/llm.py` in tre moduli
(`REVISIONE.md` voce 20). La regola 2 di `CLAUDE.md` dice di non implementare
una voce di `REVISIONE.md` se non richiesta per nome.

**Aggiunta `REVISIONE.md` voce 21:** il router non tiene alcun lock fra la
lettura della cronologia e la scrittura; e' sicuro solo perche' PTB e' a
`max_concurrent_updates=1`. Verificato, non supposto. Il lock per conversazione
va fatto come primo passo del satellite vocale, prima del secondo canale.

**Numeri:** 355 test (erano 324 a inizio sessione), copertura 98%,
`adapters/telegram.py` e `config.py` al 100%, ruff pulito.

**Commit:**
- `3cc4101` make list-all list all of it
- `166fb98` close the session log: deploy, the two commissioned jobs, and two mistakes
- `2ebdc6e` stop the telegram adapter one step at a time, and test that it does
- `16a0d8a` answer a broken install with a sentence, not a stack trace
- `8a72c00` say a true thing about how long ago something was
- `0cc50e8` notice a bot that has gone deaf
- `504bdff` make the version stamp admit when it has gone stale
- `cc4b982` record in every backup which code it holds
- `43e56fe` store and check out every text file as LF
- `ff8cbc5` test the Anthropic dialect, which never had a test of its own
- `9a5bfd6` cover the two configuration mistakes nobody had tried

---

## 2026-08-31 — Session 6

**Status:** Complete (in attesa di decisione su push e deploy)

**Context:** Continuation di Session 5. Revisione finale per la produzione, su
richiesta dell'utente: *"Il progetto deve essere stabile quindi gestire
correttamente tutte le eccezioni. Il programma deve essere fluido il piu
possibile e il codice deve essere ordinato e deve seguire le buone norme di
produzione."* Eseguita in autonomia su mandato esplicito
(*"non disturbarmi finche non hai finito"*), in tre fasi concordate:
correttezza, osservabilita, ordine.

**Done — fase 1, correttezza (ogni confine con l'esterno):**
- `core/memory.py`: cinque `assert` sostituiti con `_require_open()` — sparivano
  sotto `python -O` lasciando un `AttributeError` su `None`. `core/tasks.py` lo
  faceva gia' correttamente: i due moduli fratelli non erano d'accordo.
- `core/retry.py` estratto: la formula di backoff era ripetuta 5 volte in 2 moduli
- `main.py`: lo start-up apriva i due database **prima** del `try`, quindi un
  errore di Telegram lasciava le connessioni aperte; e lo shutdown era sequenziale,
  quindi la prima eccezione abbandonava tutto il resto. Ora si smonta esattamente
  cio' che si e' montato, in ordine inverso.
- **HTTP 429 trattato come guasto permanente** (era archiviato con i 4xx): un
  limite al minuto non veniva mai ritentato, e un tetto giornaliero veniva
  riportato come *"non riesco a contattare il cervello, riprova tra poco"* — la
  diagnosi sbagliata e un consiglio che poteva solo fallire. E' l'incidente delle
  17:32 di oggi. Ora ogni client ha un ramo `RateLimitError` **prima** di
  `APIStatusError` (stesso ordine, e stessa ragione, di `BadRequest` prima di
  `NetworkError` nell'adapter Telegram); il `retry-after` del server viene pesato
  contro `remaining_backoff()`, e `LLMQuotaExceededError` porta l'attesa fino
  all'utente: *"Riprova fra circa 11 minuti."*
- **Guasto del database prendeva giu' l'intero turno**: la lettura a inizio turno
  stava fuori dal `try`, le due scritture a fine turno non erano protette. Ora
  perdere la cronologia costa il contesto, non il turno; e non riuscire a salvare
  una risposta gia' pagata in token non la butta piu' via.
- **Un guasto imprevisto produceva silenzio**: l'error handler di PTB teneva vivo
  il processo ma non diceva niente all'utente. Ora risponde, con la stessa
  whitelist di ogni altro percorso.

**Done — fase 2, osservabilita:**
- I quattro modi in cui un turno puo' degradare avevano quattro formati di log e
  due severita'. Ora passano da un solo `_degrade()`, condividono la riga
  `turn degraded (<reason>)`, e il motivo viaggia sulla risposta.
- `TurnStats`: conteggio turni / turni degradati / ultimo motivo / da quanto.
- **`/health` sapeva solo dire "ok"**, anche a database morto. Ora legge davvero
  dallo store (la stessa operazione di ogni turno, molto piu' economica di
  `PRAGMA integrity_check`) e risponde `503 degraded` quando non ci riesce.

**Done — fase 3, ordine e copertura:**
- Testati i rami di fallimento dell'**auto-riparazione** — quarantena che non
  riesce a rinominare, snapshot che non si copia, `VACUUM` fallito, snapshot
  nuovo malato, `chmod` rifiutato, rotazione fallita. Erano tutti presi sulla
  fiducia: `core/memory.py` 87% → 97%.
- Coperti i rami connessione/5xx del client **Groq**, che li aveva solo per
  Anthropic — l'asimmetria che ha gia' fatto divergere i due client una volta.
- **263 test** (erano 192 a inizio sessione), `core/` al 97%, ruff pulito.

**Docs:** `CHANGELOG.md`, `REVISIONE.md` (voce 19), `docs/GUIDA.md` + `GUIDA.pdf`
rigenerato (41 pagine; front matter e footer erano ancora fermi a `v0.2.0`).

**Commit di questa sessione:**
- `d6b63bd` unwind a failed start-up, and cover the two modules that had no tests
- `0e5f7e3` treat a rate limit as its own kind of failure, not an outage
- `6675993` do not let the conversation store take the turn down with it
- `6667a57` answer the user even when the fault was one nobody foresaw
- `dbd25ba` make a degraded turn say why, and let /health report ill health
- `ec90095` record the observability work in the changelog
- `ca8050d` test the self-repair paths that only run on the worst day

**Done — i due punti delegati ("fai come pensi sia meglio"):**
- **`core/llm.py`**: deduplicate le due scale di `except`, ora in `_RetryLadder`
  scritta una volta e parametrizzata sul modulo SDK. I due `complete()` da 107 e
  81 righe scendono sotto le 40, nessuna clausola per-provider sopravvive, e i
  formati di log sono rimasti identici (verificati riga per riga). **Non** ho
  spezzato il file: la cucitura ovvia — 149 righe di dialetto Groq con un file di
  test dedicato — non e' una cucitura, perche' quelle funzioni dipendono dal
  vocabolario che vive li' (import circolare). Servirebbe un terzo modulo:
  `REVISIONE.md` voce 20, con lo schema pronto.
- **`/health`**: implementata la proposta C. `scripts/backup.sh` lo interroga
  prima di scrivere il manifest; l'esito va nel journal e nel `MANIFEST.txt`, e
  non fa mai fallire il backup. Provato contro un server vero nei tre stati; il
  terzo ha trovato un difetto nel codice appena scritto (`curl` stampa gia' `000`
  da solo, il fallback ne aggiungeva un secondo).
- **`.gitattributes`** aggiunto: sviluppo su Windows + deploy con `git pull` su
  Linux e' l'assetto in cui un CRLF entra in uno script che non puo'
  sopravvivergli. Uno shebang con `
` fallisce senza dire perche'.

**Done — deploy, coda e i due lavori commissionati:**
- **Deploy in produzione**, verificato: `/health` risponde `ok`/`store ok` e
  dichiara lo stesso commit che gira davvero.
- **Lavoro #5 chiuso — non era un guasto.** Nel database: `sei vivo?` ->
  "Sono un assistente virtuale...". Turno `groq call ok`, salvato in memoria
  (i turni degradati non lo sono mai). Era il prompt che chiedeva esattamente
  quello. Risolto lato prompt: la concisione vale per il lavoro, non per chi lo
  chiede. Misurato: +172 token a turno, ~5 scambi/giorno in meno sul tetto Groq.
- **Lavoro #6 fatto**: `abandon_development`, quarto tool. Non cancella — la riga
  resta `abandoned` con il motivo, come il DB corrotto messo in quarantena.
  Solo lavori aperti. Verificato contro il modello vero in produzione: turno 1
  chiede conferma nominando il lavoro, turno 2 chiama il tool, la coda resta con
  il solo lavoro giusto.
- **#3 e #4 abbandonati** (prova della coda; duplicato).
- **`list-all` non elencava tutto**: filtrava sui lavori aperti, quindi a coda
  vuota non rispondeva niente e lo storico delle decisioni era raggiungibile solo
  indovinando un id.

**Due errori miei, entrambi trovati provando invece che rileggendo:**
- Ho scritto nel prompt due istruzioni che si contraddicevano sulla conferma
  prima di abbandonare. Sulla pagina sembravano una frase sola; l'ha rivelato il
  modello vero.
- Due volte ho copiato un file sul server con `scp` invece di fare il deploy,
  disallineando lo stamp `VERSION` -- esattamente il difetto che quello stamp
  esiste per impedire, e che avevo appena finito di risolvere. Corretto con un
  deploy pulito entrambe le volte.

**Pending — richiede una tua decisione:**
- [ ] Nel messaggio del commit `0fb0b1b` e' rimasto un carattere giapponese
      (`un試`): si toglie solo riscrivendo la storia, che non faccio senza
      richiesta esplicita.
- [ ] `REVISIONE.md` voce 20: dividere `core/llm.py` in tre moduli. La
      deduplicazione e' fatta; la divisione e' corretta ma non urgente.

---

## 2026-08-31 — Session 5

**Status:** Complete

**Context:** Rigenerazione `docs/GUIDA.pdf` dopo aggiornamento manuale di `docs/GUIDA.md` (versione 1, solo testo).

**Done:**
- `docs/GUIDA.md` aggiornato dall'utente (versione 1, solo testo)
- `docs/GUIDA.pdf` rigenerato con pandoc + xelatex
- ROADMAP.md: spuntati GUIDA.pdf (v0.1.x) e GUIDA.md update (v0.2)
- **Revisione completa della guida per allinearla a v0.2.0** (la guida era ancora
  ferma a v0.1.0 in molti punti):
  - frontmatter e footer: `v0.1.0` → `v0.2.0`, data 31 agosto
  - intro e cap. 1.9: due provider (Anthropic a pagamento / Groq gratuito)
  - cap. 2.1 diagramma: `data/emma.db` nel filesystem, memory.py = SQLite
  - cap. 2.5: due implementazioni di memoria, non più "SQLite in programma"
  - cap. 2.6: retry solo sugli errori transitori
  - cap. 3.1 mappa: aggiunta `data/`, llm.py = Anthropic/Groq
  - cap. 3.3: documentata `SqliteConversationMemory` con open/close e MEMORY_DB_PATH
  - cap. 3.4: due classi client con la stessa interfaccia
  - cap. 3.7: `SqliteConversationMemory` in main.py, lifespan apre/chiude il DB
  - cap. 3.8: 21 → 43 test, con tabella per file
  - cap. 4.3/4.4/4.5/4.6: aiosqlite e groq nella verifica pip, `MEMORY_DB_PATH`
    negli opzionali, log di avvio con `provider=` e `db=`, `/health` con provider
  - cap. 5.1/5.4/5.5: memoria persistente, costo zero con Groq, rimosso il
    limite "memoria persa al riavvio"
  - **nuovo cap. 5.6**: come azzerare la memoria
  - cap. 6.1/6.5/6.6.2/6.7: log con provider, backup che include il DB,
    ripristino che riporta le conversazioni, **nuovi casi di troubleshooting
    SQLite** (permessi, database locked, unable to open) e Groq (404 modello)
  - Appendici A e C: comandi per azzerare la memoria e ispezionare il DB
- `CHANGELOG.md`: `[Unreleased]` promosso a `[0.2.0] - 2026-08-31`, link di
  confronto aggiornati, sezione Documentation aggiunta
- 43 test verdi, ruff pulito

**Done (continued) — integrità database (v0.2.1):**
- Domanda dell'utente: backup del solo DB + ripristino automatico se non riparte.
  Analisi in `REVISIONE.md` voce 16: il backup era **realmente rotto**
  (`tar` di un SQLite vivo può archiviare una transazione a metà), il mirror
  automatico generico invece è stato sconsigliato e non implementato.
- `backup.sh`: snapshot con `VACUUM INTO` + verifica integrità, `data/` escluso
  dal tar, `MANIFEST.txt` dichiara lo stato del database. Richiede `sqlite3`.
- `core/memory.py`: `journal_mode=WAL`, `integrity_check` all'apertura,
  quarantena del file rotto (mai cancellato), ripristino dallo snapshot più
  recente sano con fallback alla generazione precedente, snapshot su open e
  close. Tutto loggato a livello ERROR.
- **Vincolo di progetto:** il ripristino scatta solo su corruzione accertata,
  mai perché "il servizio non parte" (motivazione in `REVISIONE.md` 16.5).
- 8 nuovi test (51 totali), ruff pulito
- `backup.sh` verificato end-to-end con uno shim `sqlite3` su Windows:
  percorso felice, fallback senza sqlite3, esclusione di `data/`, e messaggio
  effettivamente rileggibile dall'archivio
- Docs: GUIDA cap. 1.4, 3.3 (nuova sezione auto-riparazione), 4.8, 5.6, 6.1,
  6.5, 6.6.2, 6.7 (nuovo caso), Appendici A/B; CHANGELOG; ROADMAP v0.2.1

**Done (continued) — revisione pre-pubblicazione:**

Revisione sistematica su richiesta ("assicurati che sia tutto giusto ad essere
pubblicato ed installato"). Trovati tre bug, due dei quali **bloccavano
l'installazione pulita**:

1. **`emma.service` non poteva scrivere il database.** `ProtectSystem=strict`
   senza `ReadWritePaths` rende `/opt/emma` in sola lettura: la v0.2.0
   pubblicata **falliva su un'installazione fatta seguendo la guida**.
   Funzionava su Aruba solo perché lì la unit era stata scritta a mano
   semplificata durante il deploy. Aggiunto `ReadWritePaths=/opt/emma/data`
   (la directory di installazione resta in sola lettura, per scelta).
2. **`emma-backup.service` non poteva leggere il database.** Un lettore WAL deve
   poter aggiornare il file `-shm`: l'archivio sarebbe uscito senza cronologia.
   `ReadWritePaths` esteso alla directory del database.
3. **`backup.sh` sbagliava un `MEMORY_DB_PATH` assoluto** — prefissava sempre
   la project dir mentre `config.py` onora gli assoluti. Risultato: archivio
   senza cronologia, dichiarata come "nothing to snapshot". Verificato con un
   test A/B sul codice pre e post fix.

Inoltre: errore parlante che nomina `ReadWritePaths` invece di un `OSError`
grezzo; `data/` creata in guida al 4.6 prima della unit (systemd rifiuta di
partire se `ReadWritePaths` non esiste); nota di aggiornamento da <0.2.1;
ricopia delle unit nella procedura 6.3; intestazione stantia in `requirements.txt`.

Verifiche: 51 test verdi, ruff pulito, import di tutti i moduli, avvio a freddo
(directory creata, cronologia scritta, snapshot presente), `backup.sh`
end-to-end su percorso relativo e assoluto.

**Done (continued) — deploy v0.2.1 in produzione:**

Deploy sul VPS (IPv6-only, codice via tar+scp: GitHub non è raggiungibile).
Copia di sicurezza di `.env` + `data/` + unit precedente prima di toccare nulla.
Archivio di deploy costruito escludendo `.env` e `data/`, verificato prima
dell'invio. Installato `sqlite3`, permessi `700` su `data/` e `600` sul db,
**unit systemd blindate installate al posto di quella manuale semplificata**.

Verificato in produzione:
- 51→52 test verdi sul server, servizio `active`, 0 riavvii
- log con `provider=groq`, `db=/opt/emma/data/emma.db`; `/health` con provider
- **snapshot creato all'avvio** — la prova che il fix di `ReadWritePaths`
  funziona: con la unit precedente sarebbe stato impossibile
- `journal_mode=wal` attivo, cronologia preservata (8 messaggi, integrità `ok`)
- `backup.sh` end-to-end: snapshot consistente e verificato, `data/` escluso,
  `.env` incluso, archivio `600`, servizio vivo durante il backup

Due difetti ulteriori trovati **durante** il deploy e corretti:
1. **Snapshot a `0644`** — `VACUUM INTO` usa la umask del processo, quindi un
   file con le stesse conversazioni del database usciva più permissivo del
   database stesso. Ora `chmod 600` prima della rotazione (+1 test, 52 totali).
2. **26 MB di cache pip in ogni archivio** — `/opt/emma` è anche la home
   dell'utente `emma`, quindi `~/.cache/pip` finiva nel `tar`. Escluso:
   archivio di produzione da **23 MB a 340 KB**.

**Done (continued) — backup con ripiego automatico:**

Richiesta: backup sul disco secondario se c'è, sul primario altrimenti, ma
**deve comunque avvenire**. Implementato in `backup.sh`:
- `BACKUP_DIR` esplicito (ambiente o `.env`) → onorato com'è, ripiego solo se
  non scrivibile
- nessun `BACKUP_DIR` → `/mnt/backup/emma` **solo se è davvero un filesystem
  separato** (confronto del device con `/`), altrimenti `/var/backups/emma`
- la scelta e il motivo finiscono nel log e nel `MANIFEST.txt`
- il controllo è sul mount, non sull'esistenza della directory: scrivere in un
  `/mnt/backup` non montato riempirebbe il disco di sistema e quegli archivi
  sparirebbero sotto il mount il giorno in cui il disco venisse collegato
- `--dry-run` non crea più nulla (prima il ripiego avrebbe fatto `mkdir`)

Correzioni alla unit, entrambe necessarie perché il ripiego funzionasse:
- rimosso `RequiresMountsFor=/mnt/backup`, che trasformava l'assenza del disco
  in un job fallito — l'opposto della garanzia richiesta
- `ReadWritePaths=-/mnt/backup /var/backups /opt/emma/data` (il `-` rende
  opzionale la prima: senza, systemd rifiuta di partire se non esiste)
- `ExecStartPre=+/usr/bin/install -d -o emma -g emma -m 0700 /var/backups/emma`:
  `/var/backups` è di root, quindi l'utente `emma` non poteva crearci dentro e
  il ripiego era irraggiungibile su una macchina appena installata

Verificato in produzione partendo da `/var/backups/emma` inesistente: il
servizio l'ha creata `emma:emma 0700`, il backup è avvenuto (343 KB), il
manifest dichiara destinazione e motivo, la cronologia è recuperabile
(integrità `ok`). Rilevazione del disco separato provata con `/dev/shm`.
**Timer abilitato**, prossima esecuzione alle 03:37.

**Confermato dall'utente:** EMMA risponde su Telegram dopo il passaggio alla
unit blindata (il database è passato da 8 a 12 messaggi durante la sessione).

**Done (continued) — v0.3: EMMA commissiona il proprio sviluppo:**

Progettato in conversazione e scritto in `REVISIONE.md` voce 17 prima di
toccare codice. Vincoli dell'utente: un solo bot, nessuna spesa in più, EMMA
non parla mai per prima, consenso a ogni passaggio, e **nessuna API key** — il
lato sviluppo è una sessione di Claude Code aperta sul PC, non un servizio.

- `core/tasks.py` + `tools/development.py`: coda a sei stadi e tre tool
  (`request_development`, `work_status`, `answer_question`). **Primi tool mai
  registrati sul router, e `core/router.py` non è cambiato di una riga** — il
  protocollo scritto nella v0.1 contro una lista vuota ha retto.
- `scripts/task-queue.sh`: l'unica cosa che la chiave dedicata può eseguire,
  vincolata con `command=` in `authorized_keys`. Sette verbi, mai SQL.
- `scripts/watch-tasks.sh`: attende in shell, così la sessione si sveglia solo
  quando c'è lavoro.
- Deploy su VPS: chiave ristretta installata e verificata (`whoami` e
  `cat .env` **rifiutati**), 12 messaggi preservati, 91 test verdi sul server.

**Il bug che ha reso tutto inerte.** Dopo il deploy, ispezionando il codice:
`GroqLanguageModel` accettava il parametro `tools` e **non lo usava mai**.
Nato nella v0.1.x quando la lista era vuota, il difetto non costava nulla; con
tre tool registrati significava che il modello non li vedeva nemmeno. Nessun
errore, nessun log: EMMA rispondeva a parole, indistinguibile dal
funzionamento corretto se non si va a cercare la chiamata che non c'è stata.

Corretto traducendo i due dialetti in entrambe le direzioni **dentro
l'adattatore**, dove la differenza deve stare: dichiarazioni, chiamate e
risultati cambiano forma, il router continua a parlare una lingua sola. Il
pezzo insidioso era il replay del turno agentico, che appiattiva il traffico
dei tool a prosa — lasciando il modello incapace di vedere di aver chiamato
qualcosa.

Verificato contro l'API vera, in una directory isolata sul server senza
toccare la produzione: prefisso `sviluppo:` registra, capacità mancante viene
**proposta e non registrata**, domanda di stato risposta leggendo il database.

115 test verdi, ruff pulito.

**Done (continued) — passo B3 e primo giro reale del ciclo:**
- Hook `SessionStart` (`scripts/queue-brief.sh` + `.claude/settings.local.json`):
  conta i lavori in attesa all'apertura di ogni sessione. Riporta solo il
  numero, non il testo. Tace ed esce 0 se il server è irraggiungibile.
- `scripts/task-queue.sh`: aggiunto il verbo `create` — mancava un posto dove
  mettere un difetto trovato lavorando al codice. Non sposta il controllo: si
  ferma comunque al checkpoint 1.
- **Primo giro reale**: l'utente ha commissionato il lavoro #1 da Telegram alle
  10:21, io l'ho letto dalla coda con la chiave ristretta e gli ho dato il
  checkpoint 1. Il meccanismo funziona end-to-end.

**Incidente in produzione (31/08, 13:17) — EMMA non rispondeva:**

Sintomo: servizio `active`, `incoming message` nei log, nessun `answered`.
Causa: `httpcore.ConnectTimeout` su `connect_tcp` — il processo non apriva
**nuove** connessioni verso Telegram, mentre quella del long polling, già
stabilita, funzionava. Per questo i messaggi entravano e le risposte no.

Escluso tutto il resto prima di agire: database integro, Groq raggiungibile,
`curl` come utente `emma` a 0,11s, descrittori di file 14 su 1024. Pool di
connessioni httpx incagliato → **risolto con un riavvio**.

Due lezioni registrate in `docs/GUIDA.md` (nuovo caso in 6.7):
1. **Misurare con la finestra giusta.** Avevo concluso che il riavvio non
   avesse funzionato usando `--since "10 min ago"`, che risaliva a prima del
   riavvio e ricontava i vecchi errori. Con `ActiveEnterTimestamp`: zero
   timeout. Errore mio, corretto.
2. **Fragilità di fondo trovata misurando:** su 20 connessioni a Telegram, una
   fallisce. Il VPS ha un solo indirizzo IPv6 e nessun IPv4 di ripiego. Con il
   codice attuale un invio fallito uccide il turno in silenzio, quindi ~1
   messaggio su 20 resterebbe senza risposta. Registrato come lavoro #2.

**Done (continued) — il difetto più istruttivo della giornata:**

L'utente ha chiesto a EMMA quali lavori fossero in sospeso. Ne ha riportato
**uno su due**, descrivendolo con l'interpretazione che lui aveva esplicitamente
scartato. Due cause distinte, trovate una alla volta:

**Prima causa (corretta, ma non era quella giusta).** Il tool metteva la
richiesta originale *prima* della domanda chiarificatrice. Un modello a cui il
prompt ordina di essere conciso comprime, e comprimendo tiene l'inizio — quindi
teneva le parole ambigue dell'utente e buttava il chiarimento. Corretto:
domanda per prima, richiesta accorciata dopo, più un'istruzione esplicita a non
riassumere. Commit `f0ad40a`.

**Seconda causa, quella vera.** Nei log: `tools=0`. **Il tool non veniva
chiamato affatto.** EMMA ripeteva parola per parola una risposta sbagliata data
un quarto d'ora prima e finita nella memoria persistente.

È l'interazione fra due cose che, singolarmente, funzionavano: **la memoria
(v0.2) e i tool (v0.3) si danneggiano a vicenda.** Una risposta ricavata da un
tool, una volta salvata, è indistinguibile da un fatto, e alla domanda
successiva viene riusata invece di rifare la domanda. Non è specifico dei
lavori: vale per qualunque strumento che riporti uno stato mutevole. **I test
non potevano vederlo, perché provano i pezzi separatamente.**

**Misurato**, dieci tentativi per configurazione, stessa domanda:

| Configurazione | Corrette |
| --- | --- |
| avvelenata, nessun contesto | 6/10 |
| avvelenata + contesto | 8/10 |
| pulita, nessun contesto | 9/10 |
| pulita + contesto | **10/10** |
| in produzione dopo il deploy | 5/5 |

**La soluzione, su richiesta esplicita dell'utente di non dipendere dal
modello** (*"dobbiamo pensare che l'ia possa essere diversa alla base"*):
`ContextProvider` in `core/router.py`. Un protocollo con un metodo asincrono,
interrogato **una volta per turno** (non a ogni giro di tool: lo stato non
cambia a metà turno), il cui risultato è accodato al prompt di sistema.
`DevelopmentContext` produce la riga con conteggi e numeri, e dichiara quale
fonte vince quando la memoria dissente.

Non resta nessuna decisione da sbagliare: la riga c'è comunque. Ed è testo
semplice, quindi non c'è `tool_choice` da tradurre fra i due dialetti —
cambiando provider il comportamento non degrada in silenzio. `core/` continua
a non sapere cosa sia un task. Un fornitore che esplode viene loggato e saltato.

Scartate: forzare `tool_choice` (richiederebbe di riconoscere "questa è una
domanda di stato" senza un modello: confronto di parole chiave, fragile e
legato alla lingua) e non salvare in memoria le risposte da tool (toglie il
veleno ma anche la continuità). Ragionamento completo in `REVISIONE.md` 17.10.

13 test nuovi (132 totali), commit `6c07059`, deployato e verificato.

**Cronologia ripulita** con copia di sicurezza completa in
`/root/emma-pre-ctx-20260831-140320` (20 messaggi, 2 lavori): la cancellazione
resta annullabile. Rimossi anche i due snapshot, che contenevano la stessa
cronologia e l'avrebbero riportata indietro a un eventuale recupero.

**Done (continued) — i due lavori commissionati, chiusi:**

Il ciclo ha girato per intero e in entrambe le direzioni: l'utente ha risposto
da Telegram, EMMA ha registrato, il guardiano mi ha svegliato
(`work waiting - waking the session`), ho lavorato, e le chiusure sono tornate
a lui per la stessa strada.

**Lavoro #2 — la risposta non si perde più** (commit `1960d20`). L'indicatore
"sta scrivendo" era la prima chiamata in uscita: un disturbo lì uccideva il
turno *prima* di consultare il modello. Ora è innocuo. L'invio viene ritentato
sui fallimenti transitori (3 tentativi, 1s poi 2s, la politica di `core/llm.py`)
e la consegna non è più tutto-o-niente: se un pezzo di una risposta lunga si
perde, gli altri partono. Se non arriva niente è un `ERROR` esplicito, non
silenzio.

> **Trappola trovata dai test:** in python-telegram-bot **`BadRequest` eredita
> da `NetworkError`**, quindi `except (TimedOut, NetworkError)` — scritto per
> dire "solo i transitori" — ritentava tre volte messaggi che Telegram rifiuterà
> sempre. La clausola permanente ora viene prima, con il commento che spiega
> perché l'ordine non è cosmetico.

**Lavoro #1 — EMMA sa quale codice sta eseguendo** (commit `8e71d21`, `cfaa54e`).
`core/version.py` preferisce il timbro scritto dal deploy, ripiega su git su un
checkout, e **quando non sa lo dice** invece di inventare. `/health` espone
`version`, `commit`, `built`; `main.py` non dichiara più una versione propria.

**`scripts/deploy.sh`**, deciso con l'utente al posto della variante minimale:
il timbro non è un passo da ricordare, è il deploy stesso a scriverlo. Rifiuta
di partire se l'albero è sporco (il commit timbrato mentirebbe), se test o ruff
falliscono, o se l'archivio contiene `.env` o `data/` — controllato due volte.
Primo deploy vero passato di lì: **21 secondi, un comando**, e produzione e
repository ora combaciano verificabilmente (`cfaa54e` da entrambe le parti).

**Lingua uniformata** (commit `cfaa54e`): inglese ciò che il modello legge per
decidere (nomi, descrizioni, argomenti), italiano ciò che arriva all'utente.
Il confine non è dove sembra: EMMA **cita alla lettera** le stringhe dei tool —
`ATTENDE UNA RISPOSTA DELL'UTENTE` è comparso parola per parola in chat — quindi
tradurle metterebbe frammenti inglesi davanti a un utente italiano.

**156 test verdi.**

**Confermato in produzione poche ore dopo.** Alle 14:36 una degradazione di
rete del VPS (anche Groq da <1s a 17-25s per chiamata) ha fatto fallire
l invio due volte:

```
14:36:41  telegram send failed (attempt 1/3): TimedOut
14:36:47  telegram send failed (attempt 2/3): TimedOut
14:36:49  telegram send succeeded on attempt 3
14:36:49  answered chat_id=... (60 chars)
```

Con il codice di stamattina quel messaggio sarebbe sparito in silenzio e
l utente avrebbe visto un bot morto. Terza degradazione di rete della
giornata: la fragilita di questo host e ricorrente, e ora costa qualche
secondo invece di una risposta.

**Pending:** nessuno. La chiusura di entrambi i lavori e confermata
 dall utente: la risposta plurale valeva per tutti e due.
- [ ] **Tracciabilità:** alle 13:34:58 `tools/development.py` e
      `prompts/system_prompt.txt` sono finiti in produzione (contenuto corretto,
      impronte verificate) **senza che io sappia indicare il comando che l'ha
      fatto**. Sequenza al contrario: prima in produzione, poi committato.
      Registrato perché non sparisca, non perché sia stato risolto.
- [ ] **Copie di sicurezza sul server** da potare: `/root/emma-pre-*` sono
      quattro, tutte di oggi.
- [ ] **EMMA ha perso il contesto conversazionale** (cronologia a zero). Voluto,
      ma vale la pena saperlo: a un "e allora?" non sa più a cosa ci si riferiva.

---

## 2026-08-30 — Session 4

**Status:** Complete

**Context:** Continuation di Session 3. Obiettivo: v0.2 memoria persistente SQLite.

**Done:**
- `SqliteConversationMemory` in `core/memory.py` tramite `aiosqlite`
- `MEMORY_DB_PATH` in `config.py`, `.env.example`, `docs/GUIDA.md` (Appendice B)
- `main.py`: swap da InMemory a Sqlite, open/close nel lifespan
- `aiosqlite==0.20.0` in `requirements.txt`; `data/` in `.gitignore`
- `tests/test_memory_sqlite.py`: 9 test incluso persistence-across-reopen
- 43 test totali passano, ruff pulito
- README, ROADMAP, repo About aggiornati; authorship dichiarata
- Backup `emma-20260830-230713.zip`, commit `016bbec`, push GitHub
- Deploy su Aruba VPS: servizio riavviato, memoria persistente **verificata via Telegram**

**Pending:**
- [ ] Rigenerare `docs/GUIDA.pdf` (manuale — toolchain PDF)

---

## 2026-08-30 — Session 3

**Status:** Complete

**Context:** Continuation of Session 2. Goal: implement selectable LLM provider
(Anthropic / Groq) so EMMA can run on the free Groq tier.

**Done:**
- Added `GroqLanguageModel` to `core/llm.py` (OpenAI-compatible, same retry policy)
- Extended `config.py` with `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL` support
- Updated `main.py` to select provider at boot; `/health` now exposes `"provider"`
- Updated `.env.example` with all new variables and documentation
- Added `groq==0.15.0` to `requirements.txt`
- All 33 tests passing; ruff clean
- **VM deployment**: Ubuntu Server VM (local test machine) created and running.
  Copied updated code to VM via scp, installed groq package, configured `.env`
  with `LLM_PROVIDER=groq` and Groq API key. Service confirmed starting with
  `provider=groq, model=openai/gpt-oss-120b`.
- Updated CHANGELOG.md with Groq provider entry

**Done (continued):**
- Telegram test passed — EMMA risponde correttamente tramite Groq (`openai/gpt-oss-120b`)
- Anonimizzati IP, hostname e nome personale da tutti i file tracciati
- Aggiunta Regola 7 in CLAUDE.md: privacy check obbligatorio prima di ogni push
- Aggiornato `docs/GUIDA.md`: sezione 2.8 e Appendice B con tutte le nuove variabili
- Backup: `D:\EmmaBackups\emma-20260830-170129.zip`
- Commit: `97cfe8a`

**Done (continued):**
- Deploy su VPS Aruba (solo IPv6) completato: codice copiato via scp, Python 3.12,
  venv, .env, systemd service. EMMA risponde su Telegram dal server di produzione.
- README.md aggiornato: multi-provider, compatibilità Python 3.11/3.12, layout
- ROADMAP.md aggiornato con tutti i task v0.1.x completati
- Push a GitHub (commit `c8a8c5a` + aggiornamenti repo/roadmap)

**Pending:**
- [ ] Regenerate `docs/GUIDA.pdf` (user must do this manually — PDF toolchain)

---

## 2026-08-29 — Session 2

**Status:** Complete

**Context:** Continuation of Session 1 (context window ran out). Starting with
end-of-session procedure that was not completed.

**Done:**
- Created SESSIONS.md and ROADMAP.md for cross-session tracking
- Updated project_emma memory entry (default model was stale: haiku → sonnet)
- Ran end-of-session procedure: ruff clean, 33 tests passing, backup written to
  `D:\EmmaBackups\emma-20260829-120447.zip`, initial git commit `f4e6fbd`
- Set up GitHub repo (KurinY/emma), gh CLI authenticated, push policy clarified
- **Decision:** No remote server for now. Deployment will be tested on a local
  VM (Ubuntu Server) on this Windows PC first.

**Pending:**
- [ ] Regenerate `docs/GUIDA.pdf` from `docs/GUIDA.md` (user must do this manually)

---

## 2026-08-29 — Session 1

**Status:** Complete (end-of-session procedure pending — context ran out)

**Starting point:** v0.1.0 freshly released. No Python environment on the dev
machine. Two known bugs identified during review.

**Done:**
- Set up Python 3.11 local venv via `uv` (installed with winget; PATH reloaded
  from registry)
- **Fix:** `core/llm.py` — split single `except AnthropicError` into three
  handlers; permanent 4xx errors (wrong key, bad request) now raise immediately
  instead of burning 3 s on pointless retries
- **Fix:** `adapters/telegram.py` `_split_message` — blank lines (`\n\n`) near
  a chunk-split boundary are now preserved in the next chunk instead of being
  silently dropped by the old `lstrip("\n")` approach
- **Added:** `tests/test_llm.py` — 6 tests covering retry / no-retry
  distinction (TDD: tests written first, confirmed failing, then code fixed)
- **Added:** `tests/test_telegram.py` — 6 tests for `_split_message` including
  blank-line preservation (same TDD flow)
- **Changed:** default model `claude-haiku-4-5-20251001` → `claude-sonnet-4-6`
  in `config.py`, `.env.example`, `docs/GUIDA.md` (section 2.8, variable
  tables, cost section, log examples), `CHANGELOG.md`
- All 33 tests passing; ruff clean (format + check)

**Not done (context ran out before end-of-session procedure):**
- [ ] Backup script: `powershell -ExecutionPolicy Bypass -File .\scripts\backup-dev.ps1`
- [ ] Git commit covering all changes above
- [ ] Regenerate `docs/GUIDA.pdf` (user must do this — PDF toolchain not
  available in Claude Code)

**Files changed this session:**
- `core/llm.py`
- `adapters/telegram.py`
- `tests/test_llm.py` (new)
- `tests/test_telegram.py` (new)
- `config.py`
- `.env.example`
- `docs/GUIDA.md`
- `CHANGELOG.md`
