# REVISIONE.md — revisione critica delle decisioni

**Questo documento è consultivo.** Il progetto è stato implementato esattamente
come specificato: nessuna delle alternative descritte qui è stata applicata. Le
valuti tu e decidi cosa adottare; finché non lo dici, in produzione resta la
versione specificata.

Per ogni voce trovi: la decisione così com'è stata specificata, se la ritengo la
scelta migliore per *questo* contesto (sistema personale self-hosted, hardware
modesto, progetto pubblico e manutenibile, evoluzione futura verso
SQLite/skill/voce), l'alternativa concreta, pro e contro, e un verdetto fra
**cambiamento da fare subito**, **da considerare in una fase futura** e **non
vale la pena**.

---

## 0. Correzioni e scostamenti dalla specifica

Nessun errore oggettivo della specifica ha impedito il funzionamento: non ho
dovuto correggere nulla per far girare il sistema. Ci sono però quattro
scostamenti da dichiarare.

**0.1 — Rinominato JARVIS in EMMA.** Su tua indicazione esplicita durante la
lavorazione. Il nome compare ora in `emma.service`, `emma-backup.service`,
`emma-backup.timer`, nella directory di progetto, nel default `BACKUP_DIR`
(`/mnt/backup/emma`) e nel prompt di personalità. Nessun residuo del vecchio nome è rimasto nel codice, nei percorsi,
nei nomi dei file o nella documentazione.

**0.2 — `D:\JarvisBackups` è diventato `D:\EmmaBackups`.** Conseguenza diretta
del punto precedente: la specifica indicava il primo, ma tenere un percorso di
backup col vecchio nome sarebbe stato incoerente. È comunque un parametro:
`-DestinationPath` lo sovrascrive senza toccare lo script.

**0.3 — Nessun `__init__.py` in `adapters/`, `core/` e `tests/`.** La struttura
richiesta elencava i file uno per uno e non li includeva, quindi ho usato i
namespace package impliciti (PEP 420), che con Python 3.11 funzionano
identicamente sia con `python main.py` sia con pytest (`pythonpath = ["."]` in
`pyproject.toml`). Se preferisci i package espliciti è un'aggiunta di tre file
vuoti e zero modifiche al codice.

**0.4 — Gli oggetti richiesta/risposta vivono in `core/router.py`.** La
specifica parla di "un oggetto richiesta interno standard" ma non prevede un
file dove metterlo. `AssistantRequest` e `AssistantResponse` sono quindi
definiti in `core/router.py` e importati da lì dagli adapter. L'alternativa —
un `core/models.py` dedicato — è discussa al punto 12.

---

## 1. Pattern adapter

**Decisione specificata.** Il router riceve un oggetto richiesta interno
standard (testo, user_id, conversation_id) e restituisce una risposta standard.
Nessun import di Telegram dentro `core/`.

**È la scelta migliore?** Sì, senza riserve. È la decisione più importante del
progetto e quella che paga di più nelle fasi successive: quando arriverà il
satellite vocale, il router non dovrà cambiare di una riga. Il costo oggi è
minimo (due dataclass e una conversione di dieci righe nell'adapter), il
beneficio è strutturale. In `adapters/telegram.py` l'unico punto di contatto è
il metodo `_on_text_message`, che costruisce l'`AssistantRequest` e consuma
l'`AssistantResponse`.

**Alternativa concreta.** L'unica variante che avrei considerato è rendere la
risposta più ricca fin da ora, per non doverla cambiare quando arriveranno voce
e skill:

```python
@dataclass(frozen=True, slots=True)
class AssistantResponse:
    text: str                      # ciò che va detto o scritto
    degraded: bool = False
    attachments: tuple[Attachment, ...] = ()   # immagini, file, audio
    metadata: Mapping[str, Any] = field(default_factory=dict)  # latenza, token, tool usati
```

Con `attachments` l'adapter Telegram saprebbe già inviare un'immagine e quello
vocale saprebbe che c'è un file audio da riprodurre; con `metadata` potresti
loggare costo e latenza per messaggio senza sporcare il testo.

**Pro dell'alternativa.** Evita una modifica incompatibile all'interfaccia
quando aggiungerai skill che producono file o grafici; abilita subito
osservabilità per messaggio.

**Contro.** Oggi sarebbero campi sempre vuoti: complessità pagata in anticipo
per un caso d'uso che non esiste ancora, e `Attachment` andrebbe progettato al
buio senza sapere quali skill arriveranno davvero. Il `degraded` che ho aggiunto
serve invece già adesso (distingue una risposta vera da un messaggio di
cortesia).

**Verdetto: non vale la pena** ora. Aggiungere un campo opzionale a una
dataclass frozen è un'operazione retrocompatibile: si farà quando la prima skill
avrà bisogno di restituire qualcosa che non è testo.

---

## 2. Router già in forma di ciclo agentico

**Decisione specificata.** Usare il tool-use dell'API Anthropic con il ciclo
completo (chiama → se `tool_use` esegui e rimanda → ripeti), anche con lista
tool vuota, e con una firma che permetta di registrare tool futuri senza
modificare il router.

**È la scelta migliore?** Sì. È l'altra decisione che vale davvero: scrivere il
ciclo adesso costa una trentina di righe, riscriverlo dopo significherebbe
rifare i test e ripensare la memoria. La firma `Router(llm, memory,
system_prompt, tools=(), max_tool_iterations=5)` accetta qualunque oggetto che
rispetti il protocollo `Tool` (`name`, `description`, `input_schema`, `async
run()`), quindi registrare una skill è una riga in `main.py`.

Ho aggiunto due protezioni che la specifica non chiedeva ma che il ciclo rende
necessarie, e che considero parte dell'implementazione corretta, non
un'estensione: un tetto al numero di round (`max_tool_iterations`), perché un
modello che continua a chiedere tool produrrebbe altrimenti una sequenza
illimitata e a pagamento; e il contenimento delle eccezioni dei tool, che
vengono restituite al modello come `tool_result` con `is_error`, così una skill
difettosa non fa cadere il turno.

**Alternativa concreta.** Usare il *tool runner* del SDK ufficiale
(`client.beta.messages.tool_runner`), che implementa il ciclo per conto suo: si
registrano funzioni Python decorate e il SDK gestisce chiamata, esecuzione e
reinvio.

**Pro dell'alternativa.** Meno codice nostro da mantenere; il ciclo lo aggiorna
Anthropic quando il protocollo evolve.

**Contro.** È in beta, quindi l'interfaccia può cambiare sotto di noi in un
progetto che punta alla stabilità; e soprattutto lega il *cuore* del sistema al
SDK. Oggi `core/llm.py` è l'unico file che importa `anthropic`: se un domani
volessi provare un modello locale su hardware tuo — cosa perfettamente
plausibile per un assistente self-hosted — basterebbe una seconda
implementazione di `LanguageModel`. Con il tool runner il ciclo agentico stesso
sarebbe proprietà del SDK.

**Verdetto: non vale la pena.** Il ciclo scritto a mano sta in trenta righe, è
testato offline e ci compra l'indipendenza dal fornitore. Da riconsiderare solo
se il protocollo tool-use diventasse molto più complesso di così.

---

## 3. Whitelist utente

**Decisione specificata.** Il bot risponde solo all'ID in
`TELEGRAM_ALLOWED_USER_ID` e ignora silenziosamente chiunque altro.

**È la scelta migliore?** Sì, per la v1. Il silenzio è la risposta giusta: un
"non sei autorizzato" confermerebbe a uno sconosciuto che il bot è vivo e
presidiato. L'ID numerico non è indovinabile e non cambia mai, a differenza
dello username. Il controllo è esplicito nell'handler (non un filtro PTB) solo
perché così posso loggare il tentativo a livello WARNING: se qualcuno trova il
bot, te ne accorgi da `journalctl`.

**Alternativa concreta.** Una whitelist multipla, `TELEGRAM_ALLOWED_USER_IDS`
come lista separata da virgole, parsata in `config.py` in un `frozenset[int]`,
con il controllo che diventa `user.id in allowed_ids`. Costo: cinque righe.

**Pro dell'alternativa.** Il giorno in cui volessi far usare l'assistente anche
a un familiare non serve una modifica di codice, e la memoria è già isolata per
`conversation_id` quindi le conversazioni non si mescolerebbero.

**Contro.** Multiutente vero significa anche permessi per skill (chi può
spegnere le luci? chi può leggere le note?) e quota di spesa per utente: la
whitelist plurale darebbe l'illusione di supportare più persone senza
supportarne davvero il modello di sicurezza. Meglio affrontarlo quando esistono
le skill.

**Verdetto: da considerare in una fase futura**, insieme alle skill — non prima,
perché prima è solo una lista più lunga senza semantica.

---

## 4. Configurazione solo da .env

**Decisione specificata.** `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `MAX_HISTORY_MESSAGES`,
`SYSTEM_PROMPT_PATH`, `BACKUP_DIR`, `BACKUP_KEEP`.

**È la scelta migliore?** Sì. Un file, un formato, nessuna gerarchia di
sorgenti da spiegare nella guida. `config.py` valida tutto all'avvio e fallisce
con un messaggio che nomina la variabile colpevole, così un `.env` sbagliato si
diagnostica in un colpo d'occhio invece che al primo messaggio.

Due osservazioni sull'insieme di variabili così com'è.

`BACKUP_DIR` e `BACKUP_KEEP` sono le uniche due che l'applicazione non usa mai:
le consuma `scripts/backup.sh`, che legge il `.env` per conto suo. `config.py`
le carica e le valida comunque, perché la specifica le elenca fra le variabili
di configurazione e perché così un `BACKUP_KEEP=zero` viene scoperto all'avvio
del servizio e non alle 3:30 di notte, quando il timer fallisce in silenzio.

Non ho aggiunto variabili non richieste. Le due che mi sarebbero servite più
spesso, e che quindi propongo qui, sono `LOG_LEVEL` (oggi INFO fisso: per
debuggare un problema devi modificare `main.py`) e `ANTHROPIC_MAX_TOKENS` (oggi
2048 fisso in `core/llm.py`).

**Alternativa concreta.** Sostituire il caricamento manuale con
`pydantic-settings`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")
    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-haiku-4-5-20251001"
    telegram_allowed_user_id: int
    max_history_messages: PositiveInt = 20
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
```

Validazione dichiarativa, tipi controllati, `SecretStr` che impedisce alla
chiave di finire in un log o in un traceback, `extra="forbid"` che segnala un
refuso in un nome di variabile invece di ignorarlo.

**Pro dell'alternativa.** Meno codice a mano (`config.py` passerebbe da ~200 a
~60 righe), messaggi d'errore ottimi, e `SecretStr` è una protezione reale.

**Contro.** Una dipendenza in più — anche se pydantic è già installato come
dipendenza di FastAPI, quindi il costo effettivo è solo `pydantic-settings`. E
un file scritto a mano è più leggibile per chi arriva al progetto senza
conoscere pydantic, che per un progetto pubblico didattico conta.

**Verdetto: da considerare in una fase futura.** Il momento giusto è quando le
variabili passeranno da otto a quindici (voce e skill ne porteranno parecchie):
a quel punto la validazione dichiarativa vince nettamente. Nel frattempo
suggerisco solo di aggiungere `LOG_LEVEL`, che è la variabile che rimpiangerai
la prima volta che qualcosa si comporta in modo strano in produzione.

---

## 5. Resilienza: retry con backoff

**Decisione specificata.** Retry con backoff esponenziale (3 tentativi) sulle
chiamate API; se falliscono tutti, risposta di cortesia; mai crash, mai
silenzio.

**È la scelta migliore?** Nella sostanza sì: tre tentativi con attesa 1s e 2s
assorbono un blip di rete o un 529 di sovraccarico senza che tu te ne accorga, e
il messaggio di cortesia con il processo vivo è esattamente il comportamento
giusto per un assistente domestico. Ho disattivato i retry interni del SDK
(`max_retries=0`) perché altrimenti i tentativi reali sarebbero stati 3×3 = 9,
con attese moltiplicate e log illeggibili.

C'è un punto che avrei fatto diversamente, ed è l'unico di tutta la specifica
su cui ho un'obiezione tecnica concreta: **oggi vengono ritentati anche gli
errori che non possono avere successo al secondo tentativo.** Una chiave API
sbagliata restituisce 401, e il codice attuale la ritenta tre volte, aspettando
tre secondi prima di risponderti. Funziona (il criterio di accettazione è
rispettato: ricevi il messaggio di cortesia e il processo resta vivo) ma sono
tre secondi e tre chiamate sprecate per un errore la cui risposta è già certa.

**Alternativa concreta.** Distinguere gli errori ritentabili da quelli
definitivi in `core/llm.py`:

```python
RETRYABLE = (
    anthropic.APIConnectionError,   # rete assente, DNS, TLS
    anthropic.APITimeoutError,
    anthropic.RateLimitError,       # 429
    anthropic.InternalServerError,  # 5xx
    anthropic.OverloadedError,      # 529
)

except anthropic.AnthropicError as exc:
    if not isinstance(exc, RETRYABLE):
        raise LLMUnavailableError(f"errore definitivo: {exc}") from exc
    ...backoff e ritenta...
```

Aggiungerei anche un jitter (`delay * random.uniform(0.8, 1.2)`), inutile con un
solo client ma buona pratica, e il rispetto dell'header `retry-after` sui 429,
che l'API manda e che è più affidabile di qualunque backoff calcolato da noi.

**Pro dell'alternativa.** Risposta immediata quando l'errore è di
configurazione, log più chiari (`AuthenticationError` una volta sola invece di
tre righe identiche), nessuna chiamata sprecata verso un endpoint che ci ha già
detto di no.

**Contro.** Un elenco di classi da tenere aggiornato con le versioni del SDK: se
Anthropic introduce un nuovo errore transitorio e non lo aggiungiamo, viene
trattato come definitivo e perdiamo un retry legittimo. Il comportamento
attuale, "ritenta tutto", è il più semplice e sbaglia sempre dalla parte
prudente.

**Verdetto: cambiamento da valutare subito** — è l'unica voce di questo
documento che metterei in cima alla lista. Non è un bug, è una piccola
inefficienza, ma il codice è già scritto per accoglierlo (basta la condizione
`isinstance` dentro l'`except` esistente) e migliora sia la latenza percepita
sia la leggibilità dei log. Dimmi se lo vuoi e lo applico.

---

## 6. Memoria dietro interfaccia

**Decisione specificata.** Interfaccia astratta (`get_history` / `append` /
`prune`) e implementazione in-memory con finestra scorrevole su
`MAX_HISTORY_MESSAGES`, da sostituire in futuro con SQLite senza toccare il
router.

**È la scelta migliore?** Sì per la v1, e l'interfaccia è dimensionata bene: tre
metodi, nessuno di troppo. Ho fatto due scelte implementative che vale la pena
dichiarare, perché non erano nella specifica:

- **I metodi sono `async`.** Una memoria sincrona sarebbe stata più semplice
  oggi, ma SQLite (con `aiosqlite`) e qualunque altro storage sono asincroni:
  averli già `async` è precisamente ciò che rende vera la promessa "sostituisco
  senza toccare il router".
- **La finestra non si ferma mai su un messaggio `assistant`.** Se il taglio
  lascerebbe la cronologia che inizia con una risposta dell'assistente, viene
  scartato un messaggio in più. L'API Messages rifiuta una conversazione che non
  comincia dall'utente: senza questa regola, con un `MAX_HISTORY_MESSAGES`
  dispari il sistema si sarebbe rotto a caso dopo qualche scambio.

**Alternativa concreta — ed è la domanda vera: come farei la persistenza.**

Userei **SQLite puro tramite `aiosqlite`**, senza ORM. Motivi: lo schema è di
due tabelle, SQLAlchemy porterebbe un livello di astrazione e ~30 MB di
dipendenze per risparmiare venti righe di SQL, e su un server modesto SQLite in
modalità WAL regge senza sforzo il traffico di una persona.

Schema:

```sql
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,          -- 'telegram', domani 'voice'
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    summary         TEXT                     -- riassunto del passato, vedi sotto
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    tokens          INTEGER                  -- per misurare il costo reale
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, id DESC);
```

`PRAGMA journal_mode=WAL` e `PRAGMA synchronous=NORMAL`: letture e scritture non
si bloccano a vicenda e le fsync sono poche.

`SqliteConversationMemory` implementerebbe la stessa interfaccia:
`get_history` è un `SELECT ... ORDER BY id DESC LIMIT ?` invertito in Python;
`append` è un `INSERT` più l'aggiornamento di `last_active_at`; `prune` **non
cancellerebbe nulla** — e questa è la differenza concettuale importante.

Sulla gestione della finestra di contesto la mia proposta è **troncamento
adesso, riassunti dopo, e mai cancellazione**:

1. Lo storico resta integro su disco per sempre (è il motivo per cui si mette un
   database: poter cercare "cosa ci siamo detti a marzo").
2. `get_history` restituisce solo gli ultimi *N* messaggi, esattamente come
   oggi: è il troncamento, e per il 95% delle conversazioni domestiche basta.
3. Quando una conversazione supera una soglia (diciamo 40 messaggi oltre la
   finestra), un job periodico chiede al modello *più economico* un riassunto in
   200 parole di ciò che esce dalla finestra e lo salva in
   `conversations.summary`. Il router lo antepone al system prompt come "Contesto
   delle conversazioni precedenti: ...". Costa una chiamata Haiku ogni tanto e
   dà l'illusione, molto convincente, di un assistente che ricorda.
4. Il riassunto si rigenera a partire dal riassunto precedente più i nuovi
   messaggi usciti dalla finestra, così non si rilegge mai tutto lo storico.

Backup: il file `.db` va nella directory di progetto, quindi `backup.sh` lo
prende già così com'è — ma con WAL attivo l'unico modo corretto di copiarlo a
caldo è `sqlite3 emma.db ".backup /percorso/copia.db"`, non `cp`. Quando
arriverà il database sarà l'unica riga da aggiungere allo script.

**Pro dell'alternativa.** Le conversazioni sopravvivono ai riavvii (oggi un
`systemctl restart` azzera tutto); diventa possibile cercare nel passato; il
campo `tokens` ti dice quanto spendi davvero.

**Contro.** Migrazioni dello schema da gestire a mano; un file in più da salvare
e ripristinare correttamente; e la memoria persistente porta con sé una domanda
di privacy che oggi non esiste (tutto quello che dici resta scritto su disco in
chiaro).

**Verdetto: da fare nella fase v0.2**, come già previsto dalla roadmap. È il
prossimo passo naturale e l'interfaccia è pronta ad accoglierlo.

---

## 7. Logging strutturato su stdout

**Decisione specificata.** Livello, timestamp, evento su stdout, così finisce in
journalctl via systemd.

**È la scelta migliore?** Sì. Scrivere su stdout e lasciare che sia
l'infrastruttura a decidere dove finiscono i log è la pratica giusta: niente
file da ruotare, niente permessi da gestire, e `journalctl -u emma -f` ti dà
tutto. Il formato attuale è `timestamp | LIVELLO | logger | messaggio`, leggibile
a occhio e greppabile.

**Alternativa concreta.** Log in JSON per riga, con `structlog` o un formatter
custom di venti righe:

```json
{"ts":"2026-08-29T14:03:11+02:00","level":"info","event":"message_handled",
 "conversation_id":"12345","chars_in":34,"chars_out":180,"duration_ms":1240,
 "tokens_in":420,"tokens_out":95}
```

Con journald si può anche andare oltre e usare i campi strutturati nativi
(`systemd.journal.JournalHandler`), interrogabili con `journalctl
CONVERSATION_ID=12345`.

**Pro dell'alternativa.** Diventa possibile rispondere a domande tipo "quanto ho
speso questa settimana" o "qual è la latenza mediana" con un `jq` invece che a
occhio; e il giorno in cui ci saranno più canali, filtrare per canale sarà
banale.

**Contro.** JSON è molto meno leggibile quando stai guardando i log in diretta
per capire perché il bot non risponde — che è il 90% delle volte in cui li
guarderai. E un dashboard di metriche per un sistema a un utente è
sovradimensionato.

**Verdetto: non vale la pena** finché l'utente è uno solo. Diventa interessante
quando ci saranno le skill e vorrai sapere quali usi davvero: a quel punto
suggerisco il compromesso di un secondo logger "eventi" in JSON su un file
separato, lasciando il log operativo leggibile com'è.

---

## 8. Lingua di codice e documentazione

**Decisione specificata.** Codice, commenti e docstring in inglese, docstring su
tutte le funzioni pubbliche; `GUIDA.pdf` in italiano; README e altri file di
progetto in inglese.

**È la scelta migliore?** Sì, e la regola "una lingua per pubblico, non una
lingua per file" è quella giusta. Ho applicato la specifica alla lettera con una
sola eccezione dichiarata: **`REVISIONE.md` è in italiano**. È un documento di
decisione indirizzato a te, strettamente legato a una specifica scritta in
italiano, e per la community sarebbe rumore — ma è un file di progetto, quindi
la specifica avrebbe voluto l'inglese. Se preferisci, lo traduco.

L'unica stringa italiana nel codice è il messaggio di cortesia in
`core/router.py`, con un commento che spiega perché: è l'unico testo del codebase
che leggi tu e non un programmatore.

**Alternativa concreta.** Esternalizzare tutte le stringhe rivolte all'utente in
un file (`prompts/messages.it.txt` o un dizionario in `config.py`), così la
lingua dell'assistente diventa configurabile insieme alla personalità.

**Pro dell'alternativa.** Coerenza totale (zero italiano nel codice) e un
contributore inglese potrebbe usare il progetto senza toccare `core/`.

**Contro.** Tre stringhe non fanno un sistema di internazionalizzazione, e
l'indirezione renderebbe più difficile capire cosa succede leggendo il router.

**Verdetto: da considerare in una fase futura**, se e quando qualcuno userà
davvero il progetto in un'altra lingua. Prima di allora sarebbe astrazione
gratuita.

---

## 9. Componente: il canale (Telegram, long polling)

**Decisione specificata.** `python-telegram-bot` in long polling, mai webhook,
nessuna porta esposta.

**È la scelta migliore?** Sì, e per un server domestico dietro NAT non c'è
davvero gara. Il webhook richiederebbe IP pubblico o tunnel, dominio,
certificato TLS, rinnovi, e una porta aperta verso Internet su una macchina di
casa: tutta superficie d'attacco in cambio di qualche centinaio di millisecondi
di latenza in meno. Il long polling paga solo una connessione uscente sempre
aperta, che è irrilevante.

Ho aggiunto `drop_pending_updates=True` all'avvio: dopo un riavvio ricevi un
assistente vivo, non una raffica di risposte a domande di tre ore prima. E
`allowed_updates=[Update.MESSAGE]`, che riduce il traffico inutile.

**Alternativa concreta.** Se un giorno la latenza contasse davvero, il webhook si
farebbe *senza* aprire porte: un tunnel in uscita (Cloudflare Tunnel o Tailscale
Funnel) che espone l'endpoint FastAPI già presente. Servirebbe un handler
`POST /telegram/webhook` con verifica del `secret_token`, e
`Application.process_update()` al posto dello `Updater`.

**Pro.** Latenza più bassa e nessun polling a vuoto.

**Contro.** Una dipendenza esterna in più (il tunnel) proprio nel punto in cui
il progetto vuole essere autonomo, e un endpoint pubblico da proteggere.

**Verdetto: non vale la pena.** Il long polling è la scelta corretta per questo
sistema e lo resterà anche con voce e skill.

---

## 10. Componente: struttura del progetto

**Decisione specificata.** L'albero di file indicato: moduli piatti in radice
(`main.py`, `config.py`), `adapters/`, `core/`, `prompts/`, `scripts/`,
`systemd/`, `tests/`, `docs/`.

**È la scelta migliore?** Per un progetto di questa dimensione sì: si apre la
cartella e si capisce dov'è ogni cosa senza navigare. La separazione
`adapters/` ↔ `core/` è quella che conta e c'è.

**Alternativa concreta.** Il layout `src/`, standard per i progetti Python
pubblicati:

```
src/emma/{__init__,config,main}.py
src/emma/{adapters,core,prompts}/...
tests/
pyproject.toml          # con [project] e build-backend
```

installato con `pip install -e .`, import assoluti `from emma.core.router import
Router`, entry point `emma` invece di `python main.py`.

**Pro dell'alternativa.** Impossibile importare per sbaglio il codice sorgente
invece del pacchetto installato; nomi di modulo namespaced (oggi un `config.py`
in radice potrebbe teoricamente collidere con qualcosa in `sys.path`);
pubblicabile su PyPI; è ciò che un contributore Python esperto si aspetta.

**Contro.** Aggiunge un passaggio di installazione e un livello di directory a
un progetto che si deploya con `git pull` e si legge in mezz'ora. Per un
assistente self-hosted, "clona ed esegui" vale più della correttezza formale.

**Verdetto: da considerare in una fase futura**, e precisamente il giorno in cui
volessi distribuire EMMA come pacchetto installabile. Se resta un progetto da
clonare, la struttura attuale è migliore.

Sul punto minore 0.4 (dove vivono `AssistantRequest` e `AssistantResponse`): un
`core/models.py` dedicato sarebbe leggermente più pulito, e lo consiglierei nel
momento in cui la risposta si arricchisse (punto 1) o in cui altri tipi condivisi
comparissero. Con due dataclass, tenerle accanto al router che le usa è più
leggibile che dividerle in un file da tre righe utili.

---

## 11. Componente: resilienza a livello di sistema

**Decisione specificata.** Il servizio principale (`emma.service`) con
`Restart=always`.

**È la scelta migliore?** Sì, ed è il livello giusto: il processo non deve
provare a sopravvivere a sé stesso, ci pensa systemd. Ho aggiunto `RestartSec=5s`
e `StartLimitBurst=5` in `[Unit]`: se il servizio muore cinque volte in cinque
minuti la causa non è transitoria (un `.env` sbagliato, per esempio) e continuare
a riavviare significherebbe solo martellare l'API di Telegram. Meglio fermarsi e
farsi notare.

Ho anche irrigidito il sandbox systemd (`ProtectSystem=strict`, `ProtectHome`,
`CapabilityBoundingSet=` vuoto, `RestrictAddressFamilies`, `SystemCallFilter`):
il processo può leggere la propria directory e aprire connessioni HTTPS in
uscita, nient'altro. Vale la pena saperlo perché quando aggiungerai una skill
che scrive un file dovrai aggiungere un `ReadWritePaths=`, altrimenti la scoprirai
con un `Permission denied` non ovvio.

**Alternativa concreta.** Un watchdog attivo: `Type=notify` con
`WatchdogSec=120`, e nel codice un task che chiama `sd_notify("WATCHDOG=1")` solo
dopo aver verificato che il polling di Telegram è davvero vivo. Oggi un processo
che resta in piedi ma smette di ricevere update (un bug nell'updater, una
connessione appesa) non verrebbe riavviato: dal punto di vista di systemd sta
benissimo.

**Pro.** Copre l'unico modo realistico in cui EMMA può "morire senza morire".

**Contro.** Dipendenza da `python-systemd`, e un watchdog mal tarato riavvia il
servizio mentre sta solo aspettando una risposta lenta del modello — cura peggiore
del male.

**Verdetto: da considerare in una fase futura**, se ti capiterà davvero che il
bot smetta di rispondere con il servizio *active (running)*. Prima di allora è
complessità speculativa. Un sostituto povero e immediato: l'endpoint `/health`
già esposto su loopback, interrogabile da un cron che riavvia il servizio se non
risponde.

---

## 12. Componente: stack FastAPI + uvicorn

**Decisione specificata.** FastAPI + uvicorn, processo unico, asincrono, con il
polling Telegram nello stesso event loop.

**È la scelta migliore?** Con riserva. FastAPI qui non serve a quasi nulla: la
v1 espone un solo endpoint `/health` su loopback, e `python-telegram-bot` sa già
gestire il proprio event loop da solo (`Application.run_polling()`). Il costo è
tre dipendenze (fastapi, starlette, uvicorn) e una struttura di avvio — lifespan,
`uvicorn.run` — più elaborata del necessario.

Detto questo, non è una scelta sbagliata: è una scommessa sul futuro che ha buone
probabilità di ripagarsi. Il satellite Raspberry dovrà parlare con il nodo
centrale via HTTP, e allora il server ci sarà già; un pannello web di controllo,
se mai lo vorrai, idem. E `/health` non è inutile: è il modo più semplice per
sapere se il processo è vivo senza leggere i log.

**Alternativa concreta.** `main.py` senza server HTTP:

```python
def main() -> int:
    config = load_config()
    application = build_telegram_application(config)
    application.run_polling(allowed_updates=[Update.MESSAGE])
    return 0
```

Tre dipendenze in meno, una trentina di righe in meno, e l'avvio diventa
banale. Quando servirà l'HTTP, si aggiunge FastAPI in quel momento.

**Pro dell'alternativa.** Meno superficie, meno da aggiornare, meno da spiegare
nel capitolo 3 della guida. Su hardware modesto anche qualche decina di MB di
RAM in meno.

**Contro.** Rifare l'avvio quando arriverà il satellite, e perdere `/health`.

**Verdetto: non vale la pena cambiare adesso.** L'ho scritto perché la specifica
lo chiede e perché la scommessa è ragionevole: il satellite vocale è nella
roadmap, non è ipotetico. Ma se decidessi che la v2 non avrà un satellite HTTP,
questa è la prima semplificazione da fare.

---

## 13. Componente: backup e versionamento

**Decisione specificata.** Git per le versioni, `backup.sh` + timer systemd per
gli archivi datati con rotazione, `backup-dev.ps1` per gli snapshot su Windows,
flusso PC → GitHub → `git pull` sul server.

**È la scelta migliore?** Sì, e i due livelli sono complementari nel modo giusto:
Git protegge dagli errori di modifica, gli archivi proteggono dai guasti del
disco e conservano il `.env`, che in Git non può stare. Ho aggiunto tre cose che
consideravo parte del "fatto bene":

- **verifica dell'archivio prima della rotazione** (`tar -tzf`), così un backup
  fallito non può mai cancellare quello buono di ieri;
- **`MANIFEST.txt` dentro l'archivio** con data, host e commit Git di
  provenienza — un archivio senza provenienza è difficile da usare quando serve
  davvero;
- **lettura del `.env` senza `source`**: `source .env` eseguirebbe il contenuto
  del file, e un file di dati non va mai eseguito.

**Alternativa concreta.** Sostituire `tar` con **restic** o **borgbackup**:
backup incrementali deduplicati, cifrati, con verifica integrata (`restic
check`) e ripristino di singoli file da qualunque snapshot.

```bash
restic -r /mnt/backup/emma-repo backup /opt/emma --exclude .venv
restic -r /mnt/backup/emma-repo forget --keep-daily 14 --prune
```

**Pro dell'alternativa.** Cifratura a riposo (oggi l'archivio contiene la tua
API key in chiaro su un disco che potrebbe essere rubato o rivenduto); spazio
molto minore, perché 14 copie quasi identiche vengono deduplicate; possibilità
di aggiungere una destinazione remota (S3, Backblaze) con la stessa riga di
comando, cosa che oggi non hai — se la casa va a fuoco, i backup bruciano con il
server.

**Contro.** Una dipendenza esterna da installare e aggiornare; una passphrase in
più da custodire (e se la perdi, hai perso i backup); e `tar.gz` lo apre
chiunque, ovunque, fra dieci anni, senza avere restic installato — che per un
backup è una qualità sottovalutata.

**Verdetto: da considerare in una fase futura**, con una priorità: la parte che
mi convince di più non è la deduplicazione, è **la copia fuori casa**. Anche
tenendo `tar.gz`, sincronizzare `BACKUP_DIR` verso un disco esterno che stacchi,
o verso uno storage remoto, coprirebbe l'unico scenario che oggi ti lascia
scoperto. Con la cifratura che diventa obbligatoria nel momento in cui gli
archivi escono di casa.

---

## 14. Componente: `CLAUDE.md`

Questa voce risponde punto per punto alle domande che hai posto.

**Decisione specificata.** Un `CLAUDE.md` in radice con le istruzioni permanenti
per qualunque assistente AI sul progetto: dopo ogni sessione eseguire
`backup-dev.ps1` e fare commit descrittivi; mai toccare `.env`; mai applicare
modifiche architetturali non richieste, rimandando a `REVISIONE.md`.

**È la scelta migliore?** Sì, ed è l'idea più sottile di tutta la specifica:
incorporare la disciplina nel progetto invece che nella tua memoria. Un file che
l'assistente legge da solo all'inizio di ogni sessione è l'unico modo perché una
regola sopravviva a sessioni che non condividono memoria.

**Le istruzioni sono chiare o si prestano a interpretazioni divergenti?**
Le tre regole della specifica erano chiare nell'intento ma vaghe al bordo, e
l'ambiguità è dove le sessioni divergono. Le ho scritte cercando di eliminarla:

- *"mai toccare `.env`"* poteva significare "non modificarlo" oppure "non
  leggerlo neanche". Ho scritto entrambe esplicitamente, più il divieto di
  toglierlo da `.gitignore` e di incollare segreti veri in codice, test,
  documentazione o commit.
- *"modifiche architetturali"* è la formula più a rischio: senza una
  definizione, una sessione ci vede dentro anche il rinominare una funzione e si
  blocca, un'altra ci fa passare un cambio di storage. Ho messo un elenco
  chiuso: confini fra moduli, interfacce `ConversationMemory` e `Tool`, forma
  degli oggetti richiesta/risposta, backend di storage, variabili `.env`,
  dipendenze, modello di processo, layout di deploy. Fuori da quell'elenco si
  procede.
- *"commit con messaggi descrittivi"* è un criterio soggettivo. Ho messo un
  esempio completo di messaggio buono e un elenco di messaggi rifiutati (`update`,
  `fixes`, `wip`).

**Sono troppo rigide o troppo vaghe?** Il rischio maggiore era la rigidità sulla
regola 2: presa alla lettera, "non fare modifiche architetturali" bloccherebbe
anche una correzione necessaria. Ho inserito due valvole: l'eccezione per
l'errore oggettivo che impedisce il funzionamento (con obbligo di segnalarlo in
cima a `REVISIONE.md`), e la clausola generale per cui una tua istruzione
esplicita ha la precedenza, purché l'assistente dichiari quale regola sta
mettendo da parte. Senza quella clausola, prima o poi ti troveresti un assistente
che rifiuta di fare ciò che gli hai appena chiesto citando un file.

Sul versante opposto — troppo vaghe — la regola del backup era la più debole:
"esegui `backup-dev.ps1`" non dice cosa fare se fallisce. Ho aggiunto l'ordine
esplicito (verifica → snapshot → commit → riferisci) e l'obbligo di dichiarare
il fallimento invece di saltare il passo in silenzio, che è il modo tipico in cui
questa regola si degrada.

**C'è ridondanza o contraddizione con README/CONTRIBUTING?** Ridondanza
minima e voluta su un punto solo: "le decisioni architetturali si discutono
prima" sta sia in `CONTRIBUTING.md` sia in `CLAUDE.md`, perché i due file hanno
lettori diversi (un contributore umano, un assistente) e nessuno dei due legge
per forza l'altro. Contraddizioni non ce ne sono: ho tenuto lo stile di codice
solo in `CONTRIBUTING.md` e il `CLAUDE.md` ci rimanda invece di ripeterlo, così
non possono divergere. L'unica sovrapposizione da sorvegliare è la lingua
(entrambi la dichiarano): se cambierà, vanno cambiati insieme — ed è per questo
che ho messo in `CLAUDE.md` la tabella "se cambi X aggiorna Y".

**Resterà valido quando il progetto crescerà?** In buona parte sì. Le regole 1
(segreti), 3 (backup e commit) e 4 (non cancellare alla cieca) sono indipendenti
dalla fase. Le due che invecchieranno sono:

- la **regola 2**, la cui definizione di "architetturale" è tarata sulla v1:
  quando ci saranno le skill andrà detto se aggiungere una skill è una modifica
  architetturale (secondo me no, se rispetta il protocollo `Tool`; sì, se cambia
  il protocollo);
- la **tabella della regola 5**, che elenca i documenti da aggiornare e crescerà
  con il progetto.

Sono entrambe manutenzioni di poche righe, previste dalla struttura del file.

**Alternativa concreta.** La versione che scriverei se dovessi rifarlo da capo,
in una fase più matura, è un `CLAUDE.md` **corto** — mezza pagina con le sole
regole invarianti — più una directory `.claude/` con istruzioni specializzate
caricate solo quando servono:

```
CLAUDE.md                        # 5 regole, ~40 righe: segreti, architettura,
                                 # backup+commit, non cancellare, documenta
.claude/skills/add-skill.md      # come si aggiunge un tool al router, con esempio
.claude/skills/release.md        # bump versione, CHANGELOG, tag, push
.claude/skills/deploy.md         # backup sul server, pull, restart, verifica
```

**Pro dell'alternativa.** Un file istruzioni lungo si degrada: più regole
contiene, meno peso ha ciascuna, e le ultime vengono seguite peggio delle prime.
Tenere invariante il nucleo e spostare le procedure in file caricati su richiesta
mantiene alta l'aderenza e rende le procedure più dettagliate senza costo.

**Contro.** Più file da tenere allineati; e finché il progetto è piccolo, un file
solo che leggi tutto d'un fiato è più onesto e più facile da verificare.

**Verdetto: da considerare in una fase futura** — quando arriveranno le skill,
cioè quando esisteranno procedure ripetitive che meritano una scheda propria.
Per la v1 il file unico è la scelta giusta; l'ho scritto con titoli numerati
proprio perché spezzarlo, quando servirà, sia meccanico.

---

## 15. Nota sulle licenze per le fasi future

Il progetto è MIT e le dipendenze attuali sono tutte compatibili: `anthropic`
(MIT), `fastapi` (MIT), `uvicorn` (BSD-3), `python-dotenv` (BSD-3),
`python-telegram-bot` (**LGPL-3.0**), `ruff` (MIT), `pytest` (MIT). Nessun
problema oggi, ma due cose da tenere d'occhio.

**`python-telegram-bot` è LGPL-3.0.** Usarla come libreria, importandola senza
modificarla, non contagia il tuo codice: resti MIT. Vincoli reali: se
distribuissi EMMA come binario o container con la libreria dentro, devi
permettere all'utente di sostituirla con un'altra versione (con `pip` è
automatico); e se **modifichi** la libreria, le tue modifiche vanno rilasciate
LGPL. Per un progetto che si distribuisce come sorgente su GitHub, in pratica
non cambia nulla.

**Fase voce — qui la questione è concreta.** I candidati tipici:

- **Piper** (TTS): licenza **MIT** — nessun problema. Le *voci* però hanno
  licenze proprie, spesso CC BY-SA o derivate da dataset con restrizioni: vanno
  verificate una per una e non vanno impacchettate nel repository senza
  controllare, perché una voce CC BY-SA richiede attribuzione e condivisione allo
  stesso modo.
- **whisper.cpp** (STT): MIT; i modelli Whisper di OpenAI sono MIT.
- **openWakeWord** (wake word): Apache-2.0; anche qui i singoli modelli di parola
  hanno licenze proprie.
- **Attenzione a Coqui TTS**: **MPL-2.0** per il codice, ma alcuni modelli
  preaddestrati hanno una licenza non commerciale (CPML) che vieta l'uso
  commerciale. Per uso personale va bene, ma non va distribuito con il progetto
  come se fosse MIT.
- **eSpeak NG**, se lo usassi come fallback, è **GPL-3.0**: invocato come processo
  esterno non contagia nulla, ma non va linkato né incluso.

**Regola pratica** per quando arriverà la fase 4: nessun modello, voce o peso
dentro il repository. Si scaricano al primo avvio con uno script che stampa la
licenza di ciò che sta scaricando, e si documentano in un `THIRD_PARTY.md`. Così
il repository resta MIT puro e l'utente sa cosa sta installando.

---

## 16. Integrità del database SQLite — **implementata il 31 agosto 2026**

> **Stato.** 16.1 e 16.2 sono state implementate su tua richiesta, insieme a una
> forma limitata di auto-ripristino. 16.3 resta il verdetto sul *mirror
> automatico generico*, che non è stato implementato — la differenza è
> spiegata in 16.5, aggiunto dopo l'implementazione.

**Da dove nasce.** Domanda tua: conviene fare un backup a parte del solo
database, e ripristinare automaticamente la copia buona se il servizio non
riparte? La risposta è metà sì e metà no, e nel guardarci dentro è emerso un
difetto reale nel backup attuale.

### 16.1 — Il difetto: `tar` copia un database vivo

`scripts/backup.sh` archivia l'intera directory di progetto mentre il servizio
è in funzione. `data/emma.db` viene letto pagina per pagina mentre EMMA ci
scrive: se un `COMMIT` atterra a metà lettura, l'archivio contiene un database
internamente incoerente. Il `tar -tzf` di verifica non se ne accorge — controlla
che l'archivio sia leggibile, non che il `.db` dentro sia valido.

Conseguenza pratica: gli archivi prodotti finora contengono codice e `.env`
affidabili e un `.db` che potrebbe non aprirsi. Lo scopriresti solo il giorno
del ripristino.

**Correzione.** SQLite ha il meccanismo apposta. Prima del `tar`, in
`backup.sh`:

```bash
if [[ -f "${PROJECT_DIR}/data/emma.db" ]]; then
    sqlite3 "${PROJECT_DIR}/data/emma.db" \
        "VACUUM INTO '${STAGING}/emma.db.snapshot'" \
        || log "warning: could not snapshot the database, continuing without it"
fi
```

e si esclude `data/` dal `tar`, archiviando lo snapshot al suo posto.
`VACUUM INTO` produce una copia consistente di un database in uso, senza
fermare il servizio. Aggiunge una dipendenza: il pacchetto `sqlite3`, che è
nei repository ufficiali.

Costo: sei righe e un pacchetto. Beneficio: il backup del dato diventa
affidabile invece che probabile. **Verdetto: da fare subito.**

### 16.2 — WAL e controllo d'integrità all'avvio

Due miglioramenti che si reggono da soli.

`PRAGMA journal_mode=WAL` in `SqliteConversationMemory.open()`: il
write-ahead log sopravvive molto meglio a un'interruzione brutale
(kill -9, OOM killer, mancanza di corrente) rispetto al journal di default.
Una riga, nessuno svantaggio in questo scenario a scrittore singolo.

`PRAGMA integrity_check` allo stesso punto: se il database è corrotto, EMMA
lo sposta in `emma.db.corrotto-<timestamp>`, ne crea uno nuovo vuoto, logga a
livello ERROR dove ha messo il file e riparte. **Non ripristina niente da
sola:** ti dice che è successo, conserva le prove e torna operativa. La
decisione su cosa recuperare resta tua.

Costo: una decina di righe in `open()` e un paio di test. **Verdetto: da fare
subito, insieme a 16.1.**

### 16.3 — Il mirror automatico: perché no

L'idea era: se il servizio non riparte, rimetti al suo posto l'ultima copia
buona del database. Tre obiezioni.

**La diagnosi sarebbe quasi sempre sbagliata.** Le cause reali per cui EMMA
non riparte sono, in ordine di frequenza: `.env` incompleto o malformato,
dipendenza mancante dopo un aggiornamento, errore di codice appena deployato,
percorso non scrivibile. Il database corrotto non è nemmeno in classifica. Un
ripristino automatico in tutti quei casi butta via le conversazioni recenti
senza risolvere nulla, e sostituisce un errore diagnosticabile con un
comportamento inspiegabile.

**La corruzione è rarissima con questo profilo d'uso.** Un solo processo, una
sola connessione, commit espliciti, nessuna concorrenza in scrittura: è la
configurazione più sicura possibile per SQLite. Perché si corrompa serve un
guasto fisico del disco o un kernel panic dentro una `fsync`. Con WAL attivo
(16.2) anche quella finestra si stringe.

**Il valore del dato non giustifica la macchina.** La memoria è una finestra
scorrevole di venti messaggi di conversazione. Perderla costa il contesto
recente, non un archivio con valore legale o contabile. Un sistema di recovery
automatico è codice che gira senza supervisione nel momento peggiore possibile
— l'avvio dopo un guasto — ed è quindi più capace di causare un danno di
quanto il danno che previene sia grave.

**Verdetto: non vale la pena.** Il rilevamento (16.2) dà il novanta per cento
del beneficio con il dieci per cento del rischio. Rilevare e segnalare è il
comportamento giusto; ripristinare da soli non lo è.

### 16.5 — Cosa è stato implementato, e perché non contraddice 16.3

Hai chiesto che EMMA riuscisse comunque a ripristinarsi da sola "per quanto
possibile". L'implementazione accoglie la richiesta senza rinunciare
all'obiezione, perché il confine è **su cosa fa scattare il ripristino**, non
sul ripristino in sé.

**Quello che è stato fatto.** All'apertura EMMA verifica il database con
`PRAGMA integrity_check`. Se il controllo fallisce — quindi con una diagnosi
accertata, non ipotizzata — sposta il file rotto in `emma.db.corrotto-<data>`,
ripristina lo snapshot più recente che supera lo stesso controllo, e se anche
quello è illeggibile prova la generazione precedente. Se non c'è nulla di
sano, riparte vuota. Tutto a livello ERROR nei log.

**Quello che continua a non essere fatto**, ed è il punto di 16.3: nessun
ripristino parte perché *il servizio non è partito*. Un `.env` incompleto, una
dipendenza mancante, un errore di codice appena deployato non raggiungono
nemmeno questo codice — falliscono prima, con il loro messaggio d'errore
intatto. È la distinzione che rende la differenza fra una riparazione e un
insabbiamento.

La regola in una riga: **si ripristina su una diagnosi, mai su un sintomo.**
`integrity_check` è una diagnosi; "non parte" è un sintomo con una dozzina di
cause diverse, di cui la corruzione del database è la meno probabile.

**Perdita di dati accettata.** Il ripristino riporta allo stato dell'ultimo
snapshot, scritto all'ultimo avvio o all'ultimo spegnimento pulito. I messaggi
scambiati dopo quel momento sono persi, e il log lo dice esplicitamente. Si
potrebbe stringere la finestra con uno snapshot periodico (un task asincrono
nel lifespan, o un timer systemd): è la leva giusta se un giorno la finestra
risultasse troppo larga, e non richiede di toccare la logica di recupero.

### 16.4 — Se un giorno il dato diventasse importante

Se in una fase futura EMMA conservasse note, promemoria o dati che non puoi
ricostruire, la risposta corretta non sarebbe comunque il mirror automatico,
ma: snapshot orari con `VACUUM INTO` invece che giornalieri, una copia fuori
casa (già voce 13), e un comando di ripristino esplicito documentato. La
frequenza e la destinazione sono le leve giuste; l'automatismo di recovery
resta la leva sbagliata.

---

## 17. EMMA committente del proprio sviluppo (proposta, 31 agosto 2026)

**Da dove nasce.** Tua idea: *"vorrei che EMMA utilizzasse le tue capacità di
scrittura di codice per implementarsi dall'esterno"*. Non un canale di lavoro
parallelo — quello lo avevo proposto io e sbagliavo — ma EMMA stessa come
committente: le chiedi una capacità che non ha, lei la registra, io la
implemento, lei riparte avendola.

EMMA non si modifica: è il processo in esecuzione, non può riscriversi sotto i
piedi. Ma può **commissionare la propria evoluzione e riceverla**.

Questa voce è il progetto concordato in conversazione. Nulla è implementato.

### 17.1 — I vincoli che lo definiscono

Sono tuoi, e sono quelli che hanno scartato le alternative:

| Vincolo | Cosa esclude |
| --- | --- |
| Un solo bot, EMMA | il canale di lavoro separato |
| **Per ora nessuna API key: Claude Code aperto sul PC di sviluppo** | ogni variante headless o installata come servizio |
| Nessuna spesa in più | il polling a modello acceso |
| EMMA non parla mai per prima | ogni notifica push |
| Permesso a ogni passaggio | l'esecuzione autonoma fino in fondo |
| Permessi pieni sulla macchina | i prompt di conferma locali |

Gli ultimi due sembrano contraddirsi e non lo fanno: agiscono su piani diversi.
Nessun blocco **sulla macchina**, dove non hai accesso fisico e non potresti
rispondere; consenso esplicito **in conversazione**, dove il telefono basta.

**Nessuna chiave a consumo per il lato sviluppo, per il momento.** L'esecutore è
una sessione interattiva di Claude Code aperta sul PC di sviluppo, che gira
sull'abbonamento già in uso. Niente `ANTHROPIC_API_KEY` per generare codice,
nessun demone, nessun servizio da installare, niente in esecuzione sul VPS oltre
a EMMA. Restano quindi fuori **da questa prima versione**:

- Claude Code headless sul VPS di produzione;
- un servizio systemd che lanci lavoro autonomo;
- qualunque esecuzione che consumi una chiave a pagamento.

È una scelta sul *quando*, non sul *se*: potrà cambiare, e il progetto è
costruito perché cambiarla non costi una riscrittura (17.1.1).

Una parte però non dipende dal pagamento. Il PC di sviluppo è **oggi l'unico
posto dove il lavoro può realmente avvenire**: lì ci sono il repository con la
sua storia, git configurato e la raggiungibilità di GitHub — che il VPS non ha,
essendo solo IPv6. Anche pagando, un esecutore su quel server non riuscirebbe a
pushare. Il server ospita EMMA; il PC ospita l'officina.

Finché vale questo assetto, due conseguenze:

- **Se la sessione non è aperta, non succede niente.** Non c'è un processo che
  raccoglie i task in sua assenza. Il PC acceso con la sessione viva è parte
  dell'architettura, e spegnerlo è l'interruttore generale.
- **Il consumo è quello dell'abbonamento**, contato in uso della sessione e non
  in token fatturati. È esattamente per questo che 17.4 esiste: non per
  risparmiare denaro su una bolletta, ma per non bruciare la sessione in
  risvegli a vuoto.

Da qui anche la lettura corretta del cancello *"spesa su API a pagamento"* fra i
quattro che hai scelto: allo stato attuale riguarda **EMMA**, cioè un eventuale
passaggio dal tier gratuito di Groq alle API Anthropic a consumo. Il lato
sviluppo, per ora, non ha una chiave da spendere; se un giorno l'avrà, quel
cancello coprirà anche lui.

### 17.1.1 — Cosa cambierebbe se un domani diventasse a pagamento

Vale la pena fissarlo adesso, perché è quello che tiene la porta aperta: il
progetto è **neutro rispetto all'esecutore**. La coda, i checkpoint, i tre tool
di EMMA e il modo in cui le domande ti raggiungono non sanno chi stia
lavorando dall'altra parte.

| Pezzo | Se l'esecutore cambia |
| --- | --- |
| tabella `tasks` e macchina a stati | invariati |
| i tre tool di EMMA (17.6) | invariati |
| checkpoint 1/3/4/5 e loro semantica | invariati |
| attesa a costo zero (17.4) | invariata come idea, cambia chi la esegue |
| chi raccoglie e lavora | è l'unico pezzo che si sostituisce |

Cambierebbe quindi una cosa sola: da "una sessione aperta che si risveglia" a
"un esecutore che gira da sé". Sparirebbe il rischio principale — la sessione
che muore senza che nessuno se ne accorga (17.8) — e comparirebbero i due che
oggi non abbiamo: una chiave da custodire e una spesa da limitare.

Resterebbe comunque da risolvere la raggiungibilità di GitHub, che è
indipendente dal pagamento: o l'esecutore sta sul PC di sviluppo, o serve un
host con IPv4. Non è un problema da affrontare adesso, ma è bene sapere che è
lì e che non si compra con un abbonamento.

### 17.2 — Il ciclo

```
 tu → EMMA        "vorrei che ricordassi i miei appuntamenti"
        │
        ▼
 EMMA            riconosce una richiesta di sviluppo e chiede conferma
        │        (oppure la scrivi esplicita: "sviluppo: ...")
        ▼
 tabella tasks   la richiesta resta lì, con le tue parole
        │
        ▼
 io              me ne accorgo, leggo il codice, capisco
        │
        ├──▶ CHECKPOINT 1   "ho capito così, il piano è questo. Procedo?"
        │
        ▼
 io              implemento, scrivo i test, verifico
        │
        ├──▶ CHECKPOINT 3   "fatto, test verdi, ecco il diff. Committo?"
        │
        ▼
 io              commit locale
        │
        ├──▶ CHECKPOINT 4   "committato <hash>. Pusho?"
        │
        ▼
 io              push su GitHub
        │
        ├──▶ CHECKPOINT 5   "pushato. Deployo sul VPS?"
        │
        ▼
 io              deploy, servizio riavviato
        │
        ▼
 EMMA            riparte con la capacità in più
```

I checkpoint sono **1, 3, 4, 5**: manca quello fra implementazione e commit
perché lì non c'è una decisione tua — se i test falliscono li sistemo, non ti
consulto. Il diff te lo mostro comunque, al checkpoint 3, che è il momento in
cui puoi ancora dire "hai capito male" a costo zero.

Ogni checkpoint chiede il permesso di **passare alla fase successiva**, non di
confermare quella conclusa.

### 17.3 — Come ti arrivano le domande senza che EMMA parli per prima

È il pezzo che rende compatibili "una voce sola" e "nessun messaggio non
richiesto". Le mie domande finiscono nella stessa tabella; EMMA te le riferisce
**quando gliele chiedi tu**, e la tua risposta torna indietro per la stessa
strada.

```
 tu:    EMMA, a che punto sono i lavori?
 EMMA:  #3 — implementato, 53 test verdi. Committo?
        #4 — ho capito che vuoi X. Procedo?
        #5 — in attesa dalla fase 1.
 tu:    sì al 3, il 4 no, intendevo altro
 EMMA:  Registrato.
```

Il costo è la latenza: se non chiedi per sei ore, resto fermo sei ore. Ma il
lavoro non si blocca del tutto — quello che sta prima del primo cancello
prosegue, e si ferma solo la pubblicazione, che è esattamente ciò che deve
fermarsi.

Due attenuanti, entrambe compatibili con i vincoli:

- **raggruppare**: un solo "a che punto sei?" risolve tutti i cancelli aperti,
  come nell'esempio sopra;
- **concedere in anticipo per singolo task**: *"il #4 portalo fino al push"*
  lascia il default rigido e ti dà una corsia veloce quando sai già cosa vuoi.

### 17.4 — Perché non costa di più

Il punto su cui l'idea si sarebbe rotta. Se fossi io a controllare la coda ogni
quindici minuti, pagheresti ogni risveglio a vuoto, e la quasi totalità lo
sarebbe.

Non serve che sia io a guardare. Un comando di shell in background fa il giro —
`ssh` sul VPS, una `SELECT`, se non c'è niente dorme e riprova — e **finché
dorme il modello non gira**: sta lavorando `bash`, che non consuma token. Quando
trova qualcosa il comando termina, e la sua terminazione mi risveglia.

Si consuma solo quando c'è lavoro vero: una giornata senza task costa zero
invece di novantasei risvegli inutili.

Trattandosi dell'abbonamento e non di una chiave a consumo (17.1), la cosa da
proteggere non è una bolletta ma la **capienza della sessione**: ogni risveglio
inutile consuma contesto e uso, e una sessione che deve restare aperta per
giorni non se lo può permettere. Lo stesso meccanismo che eviterebbe la spesa
evita l'esaurimento.

Lato EMMA non cambia nulla: resta sul tier gratuito di Groq, e registrare un
task è una chiamata a un tool, non una generazione.

### 17.5 — Dove vivono i task

**Nello stesso file SQLite della memoria** (`data/emma.db`), in una tabella
`tasks`, gestita da un modulo separato con una connessione propria — non
dentro `SqliteConversationMemory`, che ha un'altra responsabilità.

L'alternativa era un secondo file `data/tasks.db`. L'ho scartata per un motivo
concreto: tutto quello che è stato costruito il 31 agosto — controllo di
integrità all'avvio, snapshot con `VACUUM INTO`, ripristino dalla copia sana,
backup consistente — vale **per quel file**. Un secondo database o duplicherebbe
tutta quella macchina o ne resterebbe scoperto, e sarebbe scoperto in silenzio.
La modalità WAL rende sicuro l'accesso concorrente, quindi la mia lettura via
SSH non disturba il servizio che ci scrive.

Il rovescio da accettare: se il database venisse ripristinato da uno snapshot,
tornerebbero indietro anche i task. È coerente, ed è meglio dell'alternativa.

Schema minimo:

| Colonna | Cosa contiene |
| --- | --- |
| `id` | progressivo, è il numero con cui ne parli a EMMA |
| `created_at`, `updated_at` | quando |
| `request` | la richiesta **con le tue parole**, non riassunta |
| `stage` | dove siamo: `nuovo`, `capito`, `committato`, `pushato`, `deployato` |
| `status` | `da_prendere`, `attende_te`, `in_corso`, `chiuso`, `abbandonato` |
| `note` | quello che ti dico a questo checkpoint |
| `answer` | la tua risposta, come EMMA l'ha registrata |

### 17.6 — Cosa serve a EMMA (è v0.3)

Tre tool, tutti piccoli:

- **`commissiona_sviluppo(descrizione)`** — inserisce un task. Ci si arriva in
  due modi: il prefisso esplicito `sviluppo: ...`, oppure il riconoscimento del
  modello seguito da una tua conferma. Il secondo usa `gpt-oss-120b` per quello
  che sa fare — capire un'intenzione — e non per decidere da solo: se sbaglia
  ti costa un "no".
- **`stato_lavori()`** — elenca i task che attendono te, con le mie domande.
- **`rispondi(id, testo)`** — registra la tua risposta.

Sono esattamente i primi strumenti concreti per il router agentico, che aspetta
tool dalla v0.1: il ciclo tool-use c'è già ed è testato, la lista è vuota.

### 17.7 — Il paradosso dell'avvio

Il tool che permette a EMMA di commissionare sviluppo va scritto nel modo
normale, da me con te al PC. **Il primo anello non può auto-generarsi.** Da lì
in poi il ciclo si chiude e ogni capacità successiva può arrivare per quella
strada.

### 17.8 — Cosa può andare storto

- **La sessione muore** (riavvio, crash, contesto esaurito) e il comando in
  attesa muore con lei: tu continui a commissionare e nessuno raccoglie. È il
  rischio principale, ed è strutturale — non esistendo un servizio (17.1), non
  c'è nulla che riparta da solo. Serve un modo per accorgersene: un `last_seen`
  che aggiorno a ogni risveglio e che EMMA ti riferisce quando chiedi lo stato,
  così *"ultimo contatto: due giorni fa"* ti dice che la sessione è da
  riaprire.

  **Osservato il 31 agosto, e più grave del previsto.** Il guardiano ha fatto
  due giri regolari (13:29, 13:34, esattamente a 300 secondi) e poi si è
  fermato prima del terzo, **senza codice di uscita e senza errori**: non è
  morto per un difetto suo, è stato smontato dall'esterno. Un comando in
  background non è garantito sopravvivere a lungo alla sessione che lo ha
  avviato.

  Ne discende una divisione onesta dei ruoli, che vale la pena tenere a mente
  invece di scoprirla di nuovo:

  | Pezzo | Affidabilità |
  | --- | --- |
  | hook `SessionStart` (17.6bis) | **certa** — scatta a ogni apertura, nessun processo da tenere vivo |
  | guardiano `watch-tasks.sh` | **al meglio delle possibilità** — utile finché vive, non una garanzia |

  Il risultato pratico è comunque accettabile: apri una sessione e sai subito
  se c'è lavoro; mentre lavori, se il guardiano è vivo ti sveglia. Quello che
  **non** si può promettere è "commissiono di notte e lo trovo fatto".
- **Il contesto si esaurisce** su una sessione lunga: ricordo le decisioni, non
  ogni dettaglio. `SESSIONS.md` e `ROADMAP.md` sono la memoria vera e vanno
  aggiornati spesso, non a fine sessione.
- **Il modello debole sbaglia il riconoscimento**: contenuto dalla conferma.
- **Il task è ambiguo**: il checkpoint 1 esiste per questo. Chiedo, non
  indovino.
- **La chiave SSH sul PC** dà accesso al VPS. È già così oggi, ma in questo
  scenario la sessione la usa da sola: vale la pena che sia una chiave dedicata
  con `command=` ristretto, non quella di amministrazione.

### 17.10 — Lo stato non si chiede a un tool: si mette davanti

**Scoperto in produzione il 31 agosto**, ed è la lezione più generale di tutta
questa voce.

L'utente ha chiesto a EMMA quali lavori fossero in sospeso. Lei ne ha riportato
uno su due, descrivendolo per giunta con l'interpretazione che lui aveva
esplicitamente scartato. Nei log: `tools=0`. **Non aveva chiamato lo strumento
affatto** — aveva ripetuto, parola per parola, una risposta sbagliata data
quindici minuti prima e finita nella memoria persistente.

Ecco l'interazione che non avevamo previsto: **la memoria (v0.2) e i tool (v0.3)
si danneggiano a vicenda.** Una risposta ricavata da un tool, una volta salvata,
diventa indistinguibile da un fatto; e alla domanda successiva il modello la
riusa invece di rifare la domanda. Non è specifico dei lavori: vale per
qualunque strumento che riporti uno stato che cambia.

**Misurato, dieci tentativi per configurazione, stessa domanda:**

| Configurazione | Risposte corrette |
| --- | --- |
| cronologia avvelenata, nessun contesto | 6/10 |
| cronologia avvelenata + contesto | 8/10 |
| cronologia pulita, nessun contesto | 9/10 |

**Perché non basta istruire il modello.** Un'istruzione nel prompt è una
richiesta di collaborazione: sposta quei numeri, e li sposta di nuovo — in
modo diverso — sul modello successivo. Ritarare il prompt a ogni cambio di
provider è l'opposto della disciplina che regge questo progetto, dove il
router parla una lingua sola e sono gli adattatori a piegarsi.

**La soluzione: `ContextProvider` in `core/router.py`.** Un protocollo con un
solo metodo asincrono che restituisce lo stato attuale in una riga. Il router
lo interroga una volta per turno — non a ogni giro di tool, perché lo stato non
cambia a metà turno — e accoda il risultato al prompt di sistema.

Le proprietà che contano:

- **Non c'è nessuna decisione da sbagliare.** La riga è presente comunque; una
  memoria stantia viene contraddetta da qualcosa già sulla pagina, invece che
  da una consultazione che nessuno ha fatto.
- **`core/` continua a non sapere cosa sia un task.** Il fornitore glielo passa
  `main.py`, come i tool: la stessa disciplina della v0.1.
- **Indipendente dal provider.** È testo nel prompt, non `tool_choice` da
  tradurre fra due dialetti. Cambiando modello il comportamento non peggiora in
  silenzio.
- **Un fornitore che fallisce non costa la risposta.** Viene loggato e saltato.

**Alternative scartate.** Forzare `tool_choice` avrebbe richiesto di
riconoscere "questa è una domanda di stato" senza usare un modello — confronto
di parole chiave, fragile e legato alla lingua. Non salvare in memoria le
risposte derivate da tool toglie il veleno ma anche la continuità: EMMA
dimenticherebbe ciò che ha appena detto.

**Il limite onesto.** Nemmeno così si arriva a una garanzia: resta una
decisione del modello, e i numeri restano numeri di *questo* modello. Quello
che il fornitore dà non è un tasso migliore, è che la verità aggiornata è
sempre in vista — quindi il comportamento non degrada di nascosto il giorno in
cui il modello cambia. È stabilità strutturale, non statistica.

**Verdetto: fatto** (31 agosto 2026), insieme alla pulizia della cronologia
avvelenata, che da sola valeva tre risposte su dieci.

### 17.9 — Cosa resta fuori, deliberatamente

- **EMMA non conosce il codice.** Lei è l'accettazione, io l'officina. Darle in
  pasto il repository a ogni turno costerebbe token per un giudizio che rifarei
  comunque io, che ho davanti storia, test e roadmap.
- **Nessun deploy automatico.** È il checkpoint 5, sempre.
- **EMMA non propone miglioramenti di sua iniziativa.** Registra i tuoi.
- **Nessun secondo bot.**

**Verdetto: da fare come v0.3**, nell'ordine 17.6 → 17.5 → 17.4. Il valore non
è l'automazione — venti minuti di lavoro restano venti minuti — ma il fatto che
un'idea avuta lontano dalla tastiera non si perda, e che il tuo giudizio entri
quattro volte invece che mai.

---

## 18. Memoria di fatti persistenti (proposta, 31 agosto 2026)

**Da dove nasce.** Domanda tua: *"se le chiedo di ricordarsi che a=2, dopo 20
prompt se lo scorda?"* Sì, e peggio di come sembra — verificato eseguendo il
codice, non deducendolo.

### 18.1 — Cosa succede davvero oggi

`MAX_HISTORY_MESSAGES` conta **messaggi, non scambi**: ogni scambio ne consuma
due, quindi 20 sono circa dieci scambi. Un fatto detto all'inizio sopravvive
fino al nono e sparisce al decimo.

E non "si scorda": `SqliteConversationMemory._prune_locked` esegue una `DELETE`.
Il database non conserva tutto mostrandone venti — **ne conserva venti in
tutto**. Quel testo non è più recuperabile da nessuno, nemmeno leggendo il file.

Il criterio di sopravvivenza è l'**età**, non l'importanza: `a=2` muore insieme
a "che ore sono".

### 18.2 — Dove vanno i token, misurato

Prima di proporre qualcosa vale la pena sapere cosa costa cosa. Stima a ~4
caratteri per token, validata contro i log reali (1.927 stimati, 1.900–2.600
osservati):

| Componente | Token | Quota |
| --- | --- | --- |
| prompt di sistema | ~755 | 39% |
| dichiarazioni dei tool | ~537 | 28% |
| cronologia (20 messaggi) | ~600 | 31% |
| riga di contesto | ~35 | 2% |

**Il costo fisso è più del doppio della cronologia.** Ne segue una correzione a
un consiglio che avevo dato a voce: ridurre `MAX_HISTORY_MESSAGES` da 20 a 10
risparmia ~300 token su 1.900, cioè il **15%**, non "quasi metà". Si
perderebbe metà della memoria per un sesto del consumo: **non conviene, e la
voce esiste anche per non ripetere quell'errore.**

Le leve vere sul consumo sono il prompt di sistema e le descrizioni dei tool —
pagati anche quando l'utente scrive solo "ciao". Ma sono anche ciò che fa
decidere bene al modello, quindi accorciarli è un compromesso, non un
guadagno netto.

### 18.3 — Le strade, e perché ne resta una

| Strada | Pro | Contro |
| --- | --- | --- |
| finestra più grande | una riga nel `.env` | costo lineare, beneficio modesto (18.2) |
| **fatti persistenti** | non scadono, costano poco, indipendenti dal provider | qualcuno deve decidere cos'è un fatto |
| riassunto automatico | conserva il senso a costo ridotto | perdita imprevedibile, e **un riassunto sbagliato è peggio dell'assenza** (voce 17.10) |
| ricerca sui messaggi vecchi | storia illimitata | serve infrastruttura fuori scala per questo progetto |

Il riassunto lo scarterei per la lezione della voce 17.10: la risposta
plausibile e sbagliata è quella che nessuno pensa di verificare.

### 18.4 — La forma: un modulo, non un pezzo del core

I due punti di innesto esistono già e sono stati costruiti oggi: il protocollo
`Tool` e il protocollo `ContextProvider`. Un modulo di memoria sarebbe
`tools/memory/` con i suoi tool, il suo fornitore di contesto e la sua tabella,
registrato con **una riga in `main.py`** — e tolto togliendo quella riga.

`core/` continuerebbe a non sapere cosa sia un ricordo, come oggi non sa cosa
sia un task.

### 18.5 — Il rapporto con il memory tool di Anthropic

Esiste ed è reale: si dichiara `{"type": "memory_20250818", "name": "memory"}`,
il modello riceve operazioni su file (`view`, `create`, `str_replace`, `insert`,
`delete`, `rename`) e il backend lo implementi tu — l'SDK Python offre
`BetaAbstractMemoryTool` come base.

**Ma è uno strumento definito da Anthropic**, quindi non funziona su Groq.
Adottarlo legherebbe la memoria a un provider, che è esattamente il vincolo che
l'utente ha posto per la voce 17.10. Quello che si può prendere è **il pattern,
non l'API**: un tool per scrivere, uno per leggere, e l'iniezione nel contesto.

Si perderebbe la sofisticazione — lì il modello organizza da sé un albero di
file, qui si avrebbe una lista piatta. Per un assistente personale la lista
piatta è probabilmente sufficiente, e si può sempre approfondire dopo.

Da notare: il memory tool di Anthropic **ha lo stesso punto debole** misurato
nella voce 17.10, perché resta uno strumento che il modello deve *scegliere* di
usare. La difesa è la stessa: il fornitore di contesto.

### 18.6 — Il problema vero, che nessuna strada risolve

**Chi decide cos'è un fatto degno di memoria.**

- Lo decide il modello → sbaglia, e non è un'ipotesi: 6 volte su 10 non
  chiamava nemmeno lo strumento che aveva davanti (17.10).
- Lo decide l'utente con un prefisso esplicito (*"ricorda: a=2"*) → affidabile,
  meno magico. È la stessa scelta fatta per `sviluppo:` in 17.6, e lì ha retto.

E il problema che nessuno dei due affronta: **i fatti si contraddicono e
invecchiano.** `a=2` oggi, `a=3` fra un mese, e ora ce ne sono due. Serve una
politica dichiarata — l'ultimo vince? te lo chiede? — e una potatura, altrimenti
crescono finché non costano quanto la finestra che dovevano sostituire.

**Verdetto: da fare, ma non prima che il progetto sia stabile.** È il tool più
utile fra quelli in lista — un assistente che dimentica tutto dopo dieci scambi
resta una chat — ma va progettato con la politica dei conflitti decisa *prima*,
non scoperta dopo.

---

## 18-bis. Memoria di fatti: implementata il 1 settembre 2026

La voce 18 era una proposta; questa e' cosa e' stato costruito, e le due cose
in cui la misura ha corretto il progetto.

`tools/facts/` — chiamato **facts** e non **memory** come scritto nella 18.4,
perche' `core/memory.py` esiste gia' ed e' l'opposto: dimentica per anzianita'.
Due moduli chiamati "memoria" che dicono cose opposte sul dimenticare sono una
collisione di nomi che questo progetto ha gia' pagato una volta.

**Due tool, non tre.** Niente `recall`: tutto e' gia' nel contesto, e una terza
dichiarazione si pagherebbe a ogni turno per rispondere a una domanda di cui il
modello vede gia' la risposta.

**Le due correzioni arrivate dalla misura, non dal ragionamento:**

1. **Avevo sottostimato il costo all'utente.** Gli avevo detto +15%. Le sole
   dichiarazioni dei tool costano **303 token a ogni turno**, pagati anche a
   vuoto: il costo parte da +13% con zero fatti. La stima aveva contato i fatti
   e dimenticato gli strumenti per gestirli.
2. **Il tetto prometteva piu' di quanto potesse mantenere.** `MAX_ACTIVE_FACTS`
   era 100, ma il limite di 4.000 caratteri sul contesto iniettato ne fa entrare
   ~80: gli altri sarebbero stati salvati, contati e mai visti dal modello.
   Portato a **50**, i due limiti smettono di contendersi il ruolo — il conteggio
   vincola l'uso normale, i caratteri restano una difesa contro i fatti lunghi.

**Costo finale, misurato sul traffico di produzione:** da ~84 scambi/giorno a
~75 (nessun fatto), ~64 (trenta fatti), ~59 (al tetto).

**Verificato prima di collegare qualsiasi cosa:** i 357 test preesistenti girati
intatti, tutti verdi. Dopo il collegamento ne e' fallito uno solo — quello che
asserisce l'insieme esatto dei tool, cioe' il test che fa il suo mestiere.
Verificato in particolare che i tre store convivano sullo stesso file SQLite
senza che la finestra smetta di potare, i fatti inizino a scadere, o il
controllo d'integrita' fallisca.

## 19. Nessuno interroga `/health` (proposta, 31 agosto 2026)

Durante la revisione per la produzione ho reso onesto l'endpoint `/health`:
prima rispondeva `"status": "ok"` in ogni circostanza, database morto compreso.
Ora legge davvero dallo store prima di rispondere e restituisce `503` con
`"status": "degraded"` quando non ci riesce, insieme al conteggio dei turni e
al motivo dell'ultimo degrado.

**Resta però il problema vero: nessuno lo legge.** Ho cercato in `systemd/` e
in `scripts/` e non c'è un solo consumatore. Un endpoint di monitoraggio che
nessuno interroga non ha mai impedito un guasto — e stasera i guasti li hai
notati tu tre volte prima del servizio.

Non l'ho collegato da solo perché toccare `systemd/` o aggiungere un job
periodico significa cambiare il layout di deploy, che la regola 2 mi vieta di
fare senza che tu lo chieda. Le opzioni, dalla più leggera:

| | Come | Costo | Cosa ottieni |
| --- | --- | --- | --- |
| A | `ExecStartPost` / un timer che fa `curl -f localhost:8000/health` | una riga di unit | systemd sa che è degradata, e lo scrive nel journal |
| B | `WatchdogSec=` + `sd_notify` dal processo | una dipendenza in più (`systemd-python`) e codice nel lifespan | systemd **riavvia** EMMA quando smette di stare bene |
| C | Aggiungere il controllo a `scripts/backup.sh`, che gira già alle 03:30 | poche righe di shell, zero unit nuove | te ne accorgi entro 24 ore, e il backup sa se sta salvando un DB sano |

**Implementata la C il 31 agosto 2026** (`scripts/backup.sh`): interroga
`/health` prima di scrivere il manifest, registra l'esito nel journal e nel
`MANIFEST.txt`, e non fa mai fallire il backup — un servizio fermo è una
ragione per conservare i dati, non per saltarli. Provata contro un server vero
nei tre casi (200, 503, nessuna risposta); il terzo ha rivelato un difetto nel
codice appena scritto, perché `curl` stampa già `000` da solo e il fallback ne
aggiungeva un secondo.

**Il mio parere era:** la **C** è quella che vale di più subito e costa meno di
tutte — il job notturno esiste già, gira comunque, e ha una ragione propria per
voler sapere se il database sta bene *prima* di copiarlo. La **B** è la
soluzione giusta a lungo termine ma è l'unica che aggiunge una dipendenza, e un
riavvio automatico su un servizio che parla con te via Telegram va deciso da
te, non da me: un loop di riavvii è peggio di un servizio degradato che
risponde.

**Verdetto: la C conviene, ma è una modifica al deploy — dimmi tu.**

## 20. Spezzare `core/llm.py` (valutata e scartata, 31 agosto 2026)

Il piano di revisione diceva "spezzare `core/llm.py`", che era a 758 righe
contro le 480 del modulo successivo. Ho fatto invece un'altra cosa, e spiego
perche'.

**Cosa ho fatto.** La duplicazione, non la dimensione, era il difetto vero. I
due client avevano due scale di `except` strutturalmente identiche, ed e'
esattamente la deriva che aveva gia' prodotto un bug reale: per un'intera
release il client Groq ha ignorato ogni dichiarazione di tool, perche' la
funzione era stata aggiunta a una copia e non all'altra. Guardando i due file
separatamente quel bug non si vedeva. I due SDK hanno una tassonomia
**identica** — `APIConnectionError`, `RateLimitError`, `APIStatusError`, e una
radice che cambia solo di nome — quindi la scala ora e' scritta una volta sola
(`_RetryLadder`) e parametrizzata. I due `complete()` sono passati da 107 e 81
righe a meno di 40 ciascuno, e non resta una sola clausola `except` specifica
per provider. I formati di log sono rimasti identici, verificati riga per riga.

**Cosa non ho fatto, e perche'.** Restava da spezzare il file. Il candidato
ovvio erano le tre funzioni di traduzione del dialetto Groq: 149 righe, pure,
con un file di test dedicato (`tests/test_llm_groq_tools.py`) — cioe' tutti i
segni di una preoccupazione gia' separata nei fatti.

Non lo e'. Quelle funzioni dipendono dal vocabolario (`Message`, `TextBlock`,
`ToolUseBlock`, `LLMResponse`, `_text_of`) che vive in `core/llm.py`, e
`core/llm.py` dovrebbe importare loro: **import circolare**. La cucitura non e'
dove sembrava. Per esistere richiederebbe un terzo modulo:

| Modulo | Contenuto | Righe stimate |
| --- | --- | --- |
| `core/messages.py` | `Message`, i tipi di blocco, `LLMResponse`, `_text_of` | ~90 |
| `core/groq_dialect.py` | le tre funzioni di traduzione | ~160 |
| `core/llm.py` | protocollo, errori, `_RetryLadder`, i due client | ~530 |

E' piu' pulito. Ma e' un cambio di confini fra moduli — la regola 2 — deciso
alla vigilia di una pubblicazione, per un guadagno che a quel punto e' solo la
dimensione del file: la duplicazione era gia' sparita, nessuna funzione supera
le 40 righe tranne le traduzioni pure, la copertura e' al 94%. Un lettore che
insegue `LLMResponse` aprirebbe tre file invece di uno.

### Ripreso il 1 settembre 2026, su domanda diretta: lo spezzeresti?

No. E misurando ho cambiato idea su **quale** sia il problema.

Il file sembra grande il doppio di quello che e'. Contando alla stessa maniera
in tutti i moduli — righe totali meno docstring, commenti e righe vuote:

| Modulo | Codice vero | File |
| --- | --- | --- |
| `core/llm.py` | **407** | 798 |
| `core/memory.py` | 232 | 472 |
| `core/router.py` | 204 | 488 |
| `core/tasks.py` | 172 | 348 |
| `adapters/telegram.py` | 144 | 303 |

Il 34% di `core/llm.py` sono docstring, che in questo progetto sono deliberate.
407 righe di codice in un modulo non sono un difetto, e spezzare sulla base
delle 798 vorrebbe dire reagire a un numero che misura soprattutto prosa. In
piu' servirebbero **tre** file e non due, per l'import circolare descritto
sopra: tre file per 407 righe peggiorano la navigazione invece di migliorarla.

**Il problema vero era un altro, e il conteggio delle righe lo nascondeva.**
`_RetryLadder` chiamava `_check_rate_limit` intorno alla riga 280, e quella
funzione era definita alla 537: leggendo dall'alto si incontrava la chiamata
**trecento righe prima della sua definizione**. E' questo che fa sembrare un
file piu' lungo di quanto sia — e spezzarlo non lo risolve, lo sposta in un
altro file.

Corretto il 1 settembre 2026: le tre funzioni che classificano un guasto sono
salite sopra la scala che le usa, e il modulo ha sei separatori di sezione. Ora
si legge dall'alto in basso senza salti: errori, di cosa e' fatta una risposta,
come si giudica un guasto, la scala, i due client, la traduzione dei dialetti.
Nessun altro modulo del progetto ha separatori, perche' nessun altro e'
abbastanza lungo da averne bisogno.

**Quando spezzarlo davvero: al terzo provider.** Oggi ci sono due dialetti
(Anthropic nativo, e Groq che parla OpenAI). Un terzo renderebbe la traduzione
la preoccupazione dominante del file, e a quel punto la cucitura vale il terzo
modulo che costa. E' un criterio verificabile, non un "piu' avanti".

**Verdetto: deduplicazione e riordino convengono e sono fatti. La divisione in
tre moduli non conviene ora — rifarsi la domanda quando arriva un terzo
provider.**

## 21. Un turno alla volta, e nessuno lo scrive (proposta, 1 settembre 2026)

`Router.handle()` legge la cronologia, poi passa **secondi** dentro il modello,
poi scrive le due righe. Fra la lettura e la scrittura non tiene nessun lock.
Due turni contemporanei sulla stessa conversazione intreccerebbero le scritture:
nel migliore dei casi l'ordine dei messaggi salvati non e' quello reale, nel
peggiore la finestra scorrevole taglia via la domanda e lascia la risposta.

**Oggi non succede**, e l'ho verificato invece di sperarlo: l'adapter costruisce
l'applicazione PTB con i valori di default, e in python-telegram-bot 22.8
`max_concurrent_updates` vale **1**. Gli update sono serializzati, quindi esiste
al piu' un turno alla volta. I due store (`SqliteConversationMemory` e
`TaskStore`) hanno ciascuno il proprio `asyncio.Lock` e `append` tiene il lock
su insert+prune, quindi la singola operazione e' gia' atomica: manca solo
l'atomicita' del *turno*.

Il problema e' che questa correttezza dipende da un valore di default di una
libreria, che nessun file dichiarava. Ho aggiunto il commento in
`core/router.py`; questa voce e' il seguito.

**Cosa la romperebbe**, in ordine di probabilita':

| | Cambiamento | Effetto |
| --- | --- | --- |
| 1 | Il **satellite vocale sul Raspberry** (gia' in roadmap) | secondo canale, secondo turno in parallelo: la rompe |
| 2 | `concurrent_updates=True` per far rispondere il bot mentre lavora | la rompe |
| 3 | Un secondo utente in whitelist | conversazioni diverse, quindi righe diverse: non la rompe |

**La correzione**, quando servira': un `dict[str, asyncio.Lock]` per
conversazione nel router, preso attorno all'intero turno. Non attorno alle sole
scritture — sarebbe inutile, perche' il problema e' che la cronologia letta
all'inizio e' gia' vecchia quando si scrive. Costo: una decina di righe, e la
serializzazione per conversazione che gia' esiste di fatto diventa dichiarata.

**Verdetto: non farlo ora** — sarebbe codice che protegge da una condizione che
non puo' verificarsi, e non testabile senza fabbricare la concorrenza che non
c'e'. **Farlo come primo passo del satellite vocale**, prima di aggiungere il
secondo canale, non dopo.

## 22. Il deploy non toglie mai niente (proposta, 1 settembre 2026)

Scoperto guardando perche' il controllo della voce 19-bis segnalava un file su
un deploy appena fatto. La causa immediata era mia ed e' corretta; questa e'
l'altra cosa che si e' vista strada facendo.

Il passo remoto di `scripts/deploy.sh` fa:

    tar -xzf /tmp/emma-deploy.tar.gz -C /opt/emma

`tar` **sovrascrive, non sincronizza**. Un file cancellato dal repository non
viene mai rimosso dal server: resta li' per sempre. Le conseguenze, in ordine
di gravita':

1. **Un modulo Python cancellato resta importabile.** Se domani si toglie
   `tools/introspection.py` e qualcosa lo importa ancora per errore, in
   sviluppo l'import fallisce subito e in produzione **funziona**, eseguendo
   codice che non esiste piu' in nessun commit. E' il tipo di divergenza che
   rende irriproducibile un bug.
2. Residui che nessuno ha mai spedito consapevolmente. `/opt/emma/.pytest_cache`
   c'e' oggi (56K) e `.cache` pure, entrambi gia' nella lista di esclusione
   dell'archivio: sono arrivati prima che quella lista esistesse e non se ne
   sono piu' andati.
3. Un file rinominato esiste in produzione sotto entrambi i nomi.

**Le opzioni**, dalla piu' leggera:

| | Come | Rischio |
| --- | --- | --- |
| A | Prima di estrarre, cancellare le sole directory interamente spedite (`core`, `adapters`, `tools`, `tests`, `scripts`, `docs`, `prompts`, `systemd`) | basso, ma se l'estrazione fallisce subito dopo l'installazione resta rotta |
| B | Estrarre in `/opt/emma.new`, poi scambiare le directory con `mv` | il passo di scambio non e' atomico per `.env`, `data/` e `.venv`, che vanno reinnestati |
| C | `rsync --delete` con le esclusioni, invece di `tar` | il piu' pulito e il piu' corretto; richiede `rsync` sul server e riscrive meta' dello script |

**Il mio parere: la C.** E' l'unica in cui "cosa deve esserci sul server" e'
scritto in un posto solo invece di essere la somma di tutti i deploy passati.
La A e' un cerotto che sposta il rischio sul momento peggiore. La B e'
complicata proprio dove non deve esserlo.

Non l'ho fatta perche' e' una riscrittura della strada per cui passa ogni
messa in produzione, e cambiarla all'una di notte subito dopo un deploy
riuscito non e' una buona idea. Da fare a mente fresca, con un deploy di prova
verso una directory finta prima di puntarla a `/opt/emma`.

**Verdetto: la C conviene, ma va fatta da sveglio e provata a vuoto prima.**

## 23. Accorgersi di un lavoro commissionato a sessione aperta (1 settembre 2026)

L'utente ha inserito un lavoro nella coda mentre la sessione era aperta, e non
me ne sono accorto. Non e' stata una distrazione: **non esisteva un meccanismo
che potesse dirmelo.**

C'era un solo hook, `SessionStart`, che esegue `scripts/queue-brief.sh`
all'apertura della sessione e mai piu'. Una sessione che dura ore non ha modo
di sapere che nel frattempo la coda e' cambiata. I due lavori #5 e #6 di ieri
sera li ho scoperti per caso, perche' stavo abbandonando il #4 e ho eseguito
`ssh emma-queue list` per un'altra ragione.

**I tre casi, e cosa copre ciascuno:**

| Quando arriva il lavoro | Prima | Adesso |
| --- | --- | --- |
| Prima che la sessione si apra | `SessionStart` | uguale |
| A sessione aperta, e poi l'utente scrive | **niente** | `UserPromptSubmit` |
| A sessione aperta, e l'utente non scrive | **niente** | watcher in background, a richiesta |

**Cosa ho fatto.** `queue-brief.sh` prende ora il nome dell'evento come
argomento (Claude Code scarta l'output il cui `hookEventName` non corrisponde
all'hook che lo ha eseguito) e il timeout di connessione come secondo. I due
chiamanti ne vogliono uno diverso: all'avvio dieci secondi spesi per sapere
sono gratis, su ogni messaggio sono dieci secondi di attesa dell'utente, quindi
quel chiamante passa quattro. Misurato: 600-700 ms a caldo, 1,5 s a freddo,
1,4 s quando il server e' irraggiungibile — e in quel caso esce 0 senza
stampare niente, cosi' il messaggio parte comunque.

Il nome dell'evento finisce dentro JSON costruito a mano, quindi viene
validato: un apice li' produrrebbe output che Claude Code non sa leggere, e
fallirebbe **in silenzio** — il modo peggiore in cui puo' fallire una notifica.

**La terza riga resta scoperta di default, ed e' onesto dirlo.** Se il lavoro
arriva e l'utente non scrive nulla, nessun hook scatta: gli hook sono reazioni
a eventi della sessione, e "non succede niente" non e' un evento.
`scripts/watch-tasks.sh` esiste per questo — interroga la coda ed esce appena
c'e' lavoro — ma va avviato esplicitamente in background dalla sessione, muore
con essa, e ieri sera si e' fermato da solo dopo due cicli. E' best-effort per
costruzione (voce 17.8: dietro non c'e' nessun servizio).

### Completata il 1 settembre 2026: anche il terzo caso

Su richiesta dell'utente ("imposta il riavvio automatico del watcher, e rendilo
persistente"). Claude Code ha il meccanismo esatto: un hook `asyncRewake` gira
in background e sveglia il modello quando il comando **esce con 2**. Non
serviva un servizio: serviva rendere `watch-tasks.sh` adatto a essere quel
comando. Tre ostacoli, due dei quali erano trappole vere.

**Il codice 2 significava l'opposto.** Nel modo normale vuol dire "ho rinunciato
dopo sei ore". Collegato cosi', avrebbe svegliato la sessione precisamente
quando non c'era niente da dire. Il modo hook li inverte e lo documenta.

**Si sarebbe avvitato.** Il `Stop` riarma il watcher a ogni turno; con lo stesso
lavoro ancora in coda avrebbe svegliato, riavviato, risvegliato — per sempre,
se quel lavoro aspetta una risposta. Ora ricorda gli id annunciati, e un
lucchetto (con il pid verificato, non creduto) rende il riarmo idempotente.

**Il terzo era mio:** `break` dentro un `case`, che non e' un ciclo, quindi
usciva dal `while` e il watcher moriva dopo cinque secondi in silenzio. Trovato
da un test che chiedeva "e' ancora vivo?", non dalla rilettura.

**La cache locale, e un errore di progetto che ho corretto da solo.** Avevo
proposto che l'hook *leggesse* una cache invece di interrogare il server:
istantaneo. E' sbagliato — una cache vecchia di cinque minuti puo' non
contenere il lavoro appena inserito, cioe' il difetto che tutto questo esiste
per chiudere. L'ordine giusto e' l'inverso: **prima il server, la cache solo se
non risponde**, dichiarando quanto e' vecchio il dato. Un'attivita' pianificata
ogni 5 minuti la tiene tiepida anche a sessione chiusa.

**L'attivita' pianificata e' durata un'ora.** L'utente ha visto una finestra di
terminale lampeggiare ogni cinque minuti: `-Hidden` in
`New-ScheduledTaskSettingsSet` nasconde l'attivita' nell'elenco, non la
finestra, e con `LogonType: Interactive` gira dentro la sessione dell'utente.
Non potevo accorgermene — non vedo lo schermo — ed e' l'unica classe di difetto
in cui l'utente e' l'unico strumento di misura disponibile.

Disattivata, non eliminata, su sua richiesta. E la valutazione va rifatta con il
costo vero sul piatto: la cache e' gia' riscritta a ogni messaggio e a ogni
apertura di sessione, quindi l'attivita' aggiungeva soltanto aggiornamenti
mentre nessuna sessione e' aperta — cioe' quando non c'e' nessuno da avvisare.
Un fastidio permanente per un guadagno marginale e' uno scambio sbagliato, e
l'avevo proposto io definendolo "la meta' piu' piccola" senza sapere che aveva
anche un costo visibile.

Anche li' un difetto trovato provando: sotto `set -o pipefail`, `grep` che non
trova nulla esce 1, quindi una **coda vuota** era indistinguibile da un server
irraggiungibile — e con la coda appena svuotata lo script annunciava "5 lavori"
letti dalla cache. Esattamente il contrario del suo scopo.

**Verdetto: fatti tutti e tre i casi.** Il terzo resta legato alla sessione —
muore con essa, e fra la sveglia e il riarmo c'e' una finestra di pochi
secondi. Renderlo garantito vorrebbe dire un servizio che sopravvive alla
sessione: l'infrastruttura che questo progetto ha scelto di non avere. Renderlo affidabile vorrebbe dire un servizio che sopravvive
alla sessione, cioe' esattamente l'infrastruttura che questo progetto ha scelto
di non avere.

## Riepilogo dei verdetti

| # | Voce | Verdetto |
|---|------|----------|
| 1 | Pattern adapter | non vale la pena (risposta più ricca: quando servirà) |
| 2 | Router agentico scritto a mano | non vale la pena cambiare |
| 3 | Whitelist a utente singolo | fase futura, con le skill |
| 4 | Config da `.env` a mano | fase futura (pydantic-settings); `LOG_LEVEL` prima |
| 5 | Retry indiscriminato | **da valutare subito**: distinguere errori definitivi |
| 6 | Memoria in-memory | fase v0.2: SQLite + WAL, troncamento e riassunti |
| 7 | Log leggibili su stdout | non vale la pena (JSON solo con le skill) |
| 8 | Lingue | fase futura (stringhe esternalizzate) |
| 9 | Telegram long polling | non vale la pena cambiare |
| 10 | Struttura piatta del progetto | fase futura (`src/`) solo se diventa pacchetto |
| 11 | `Restart=always` | fase futura (watchdog `sd_notify`) |
| 12 | FastAPI + uvicorn | non vale la pena cambiare, ma è la prima semplificazione possibile |
| 13 | Backup `tar.gz` | fase futura: priorità alla copia fuori casa, poi restic |
| 14 | `CLAUDE.md` unico | fase futura: nucleo corto + `.claude/skills/` |
| 16.1 | `tar` di un DB vivo | **fatto** (31/08/2026): `VACUUM INTO` verificato in `backup.sh` |
| 16.2 | WAL + `integrity_check` all'avvio | **fatto** (31/08/2026), con ripristino da snapshot |
| 16.3 | Mirror automatico generico | non implementato: si ripristina su diagnosi, non su sintomo (16.5) |
| 16.4 | Snapshot periodici | fase futura, se la finestra di perdita risultasse troppo larga |
| 17 | EMMA committente del proprio sviluppo | **da fare come v0.3**: tre tool, coda nel database, checkpoint 1/3/4/5 |
| 18 | Memoria di fatti persistenti | fase futura |
| 23 | Accorgersi di un lavoro a sessione aperta | **fatti tutti e tre i casi**; l'attivita' pianificata e' stata disattivata: finestra lampeggiante per un guadagno marginale |
| 22 | Il deploy sovrascrive e non sincronizza | **la C** (`rsync --delete`): un modulo cancellato resta importabile in produzione |
| 21 | Lock per conversazione nel router | non ora; **primo passo del satellite vocale**, prima del secondo canale |
| 20 | Spezzare `core/llm.py` in tre moduli | **no**: 407 righe di codice su 798, il resto e' documentazione. Fatti deduplicazione e riordino; rivalutare al terzo provider |
| 19 | Collegare qualcosa a `/health` | **implementata la C**: controllo dentro `backup.sh`, il 31 agosto 2026 |

La voce 5 è stata implementata nella v0.1.x (retry solo sugli errori
transitori). Le 16.1 e 16.2 sono state implementate il 31 agosto 2026. Tutto il
resto è materiale per le fasi che hai già in roadmap.
