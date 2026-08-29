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

Una sola voce, la 5, la metterei in lavorazione adesso. Tutto il resto è
materiale per le fasi che hai già in roadmap.
