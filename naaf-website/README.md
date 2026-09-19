# Naaf — website (Flask)

Python/Flask-versie van de Naaf-website, inclusief:

- De volledige homepage (hero, hoe-het-werkt, expertise, principes, FAQ)
- Een intakeformulier voor **bedrijven** om een opdracht/vacature aan te melden
- Een aanmeldformulier voor **ICT-professionals**
- Een **opdrachtenpagina** (`/opdrachten`) met alle openstaande opdrachten, aanklikbaar voor de volledige omschrijving en een sollicitatieformulier (inclusief cv-upload en per-eis/wens toelichting)
- Een met wachtwoord beveiligd **beheerpaneel** (`/admin/opdrachten`) waar jij zelf opdrachten plaatst, online/offline zet en binnengekomen sollicitaties + cv's bekijkt
- Een **"Automatisch invullen met AI"**-knop bij het plaatsen van een opdracht: plak de vacaturetekst (bijv. van Flextender) of upload de pdf, en de meeste velden worden voorgevuld — het tarief vul je altijd zelf in
- Opslag van elke aanvraag, aanmelding en sollicitatie in een lokale SQLite-database (`naaf.db`)
- Automatische e-mailnotificatie naar jouw inbox bij elke nieuwe aanvraag/sollicitatie (met cv als bijlage), plus een bevestigingsmail naar de aanvrager

## Installeren en starten

```bash
# 1. Ga naar de projectmap
cd naaf-website

# 2. Maak een virtuele omgeving aan (aanbevolen)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Installeer de dependencies
pip install -r requirements.txt

# 4. Kopieer het voorbeeldbestand voor instellingen
cp .env.example .env
# ... en vul in ieder geval MAIL_TO en de SMTP-gegevens in als je e-mail wilt versturen

# 5. Start de website
python app.py
```

De site draait daarna op **http://127.0.0.1:5000**.

## E-mail instellen

Zonder SMTP-gegevens werkt de site gewoon door: aanvragen worden nog steeds
opgeslagen in de database, alleen wordt er geen e-mail verstuurd (dit wordt
gelogd in de terminal, zodat je het niet per ongeluk mist tijdens het
testen).

Zodra je een zakelijk e-mailadres hebt (bijv. `hallo@naaf.nl`), vraag je bij
je hostingpartij of domeinregistrar de SMTP-gegevens op en vul je die in
`.env` in. Veelgebruikte instellingen:

| Provider | SMTP_HOST | SMTP_PORT |
|---|---|---|
| Gmail (met app-wachtwoord) | smtp.gmail.com | 587 |
| Microsoft 365 / Outlook | smtp.office365.com | 587 |
| TransIP | smtp.transip.email | 587 |
| Vimexx / eigen hosting | vraag na bij je provider | meestal 587 |

## Opdrachten plaatsen (beheerpaneel)

Zodra een bedrijf een opdracht bij je aanmeldt (via het formulier of via
e-mail), plaats je 'm zelf op de site via het beheerpaneel:

1. Ga naar **http://127.0.0.1:5000/admin/opdrachten**
2. Log in met het wachtwoord dat je bij `ADMIN_PASSWORD` in `.env` hebt gezet
3. Klik op **"Nieuwe opdracht plaatsen"**
4. Gebruik bovenaan het formulier de **"Automatisch invullen met AI"**-box:
   plak de vacaturetekst (bijv. gekopieerd van een Flextender-opdracht) of
   upload de pdf, en klik op "Vul automatisch in". De titel, het
   functiegebied, locatie, uren, samenvatting, omschrijving en de
   kandidaat-gerichte eisen/wensen worden dan voorgevuld.
5. **Controleer alles** — de AI kan een keer iets missen of verkeerd
   interpreteren — en **vul altijd zelf het tarief in**. Dat veld wordt
   bewust nooit automatisch ingevuld: het tarief is een bewuste,
   commerciële keuze die jij maakt, geen feit dat uit de tekst te halen is.
6. Klik op **"Opdracht plaatsen"** om 'm definitief te publiceren.

Werkt liever helemaal handmatig? Vul het formulier gewoon in zonder de
AI-knop te gebruiken — die is puur een hulpmiddel, geen verplichte stap.

### AI-invullen instellen

Deze functie gebruikt de gratis Groq API (geen creditcard nodig). Zonder een
API key blijft de rest van de site gewoon werken — je krijgt dan alleen een
duidelijke melding als je op "Vul automatisch in" klikt.

1. Maak een gratis account op [console.groq.com](https://console.groq.com)
2. Maak daar een API key aan
3. Zet 'm in je `.env`-bestand bij `GROQ_API_KEY=...`
4. Herstart de site (`python app.py`)

Groq's gratis laag is ruim voldoende voor dit gebruik (tot 1.000 aanvragen
per dag) — voor het aantal keer dat jij een opdracht plaatst, ga je dat nooit
tegenkomen.

Vanuit hetzelfde dashboard kun je een opdracht met één klik offline zetten
(bijvoorbeeld zodra de opdracht is ingevuld) en zie je per opdracht hoeveel
sollicitaties er zijn binnengekomen, inclusief motivatie, antwoorden op de
eisen/wensen en een downloadlink voor elk cv.

De site start standaard met **3 voorbeeldopdrachten** zodat je meteen kunt
zien hoe alles eruitziet. Zodra je zelf een eerste echte opdracht plaatst,
kun je de voorbeelden gewoon offline zetten via het dashboard.

## Waar komen de aanvragen terecht?

Elke aanvraag wordt weggeschreven in `naaf.db` (SQLite):

- `bedrijf_aanvragen` — opdrachten die bedrijven via het formulier hebben aangemeld
- `professional_aanmeldingen` — aanmeldingen van ICT-professionals via het formulier
- `opdrachten` — de opdrachten die jij zelf via het beheerpaneel hebt geplaatst
- `sollicitaties` — sollicitaties van professionals op een specifieke opdracht

Geüploade cv's staan (buiten de website om, niet publiek toegankelijk) in
`uploads/cvs/`, en zijn alleen te downloaden via het beheerpaneel.

Je kunt de database met elke SQLite-viewer openen (bijv. [DB Browser for
SQLite](https://sqlitebrowser.org/), gratis), of vanaf de command line:

```bash
sqlite3 naaf.db "SELECT * FROM bedrijf_aanvragen;"
sqlite3 naaf.db "SELECT * FROM professional_aanmeldingen;"
sqlite3 naaf.db "SELECT titel, functiegebied, actief FROM opdrachten;"
sqlite3 naaf.db "SELECT naam, email, opdracht_id FROM sollicitaties;"
```

Daarnaast krijg je bij elke aanvraag ook direct een e-mail op het adres dat
je bij `MAIL_TO` hebt ingevuld (zodra SMTP is ingesteld), zodat je niets
hoeft te missen.

## Structuur van het project

```
naaf-website/
├── app.py                  Flask-app: routes, database, e-mail, CSRF-beveiliging
├── requirements.txt        Python-dependencies
├── .env.example            Voorbeeldinstellingen (kopieer naar .env)
├── naaf.db                 SQLite-database (wordt automatisch aangemaakt)
├── templates/
│   ├── base.html                  Navigatie, footer, gedeelde opmaak
│   ├── index.html                 Homepage + de twee intakeformulieren
│   ├── opdrachten.html            Overzicht van alle openstaande opdrachten
│   ├── opdracht_detail.html       Eén opdracht + sollicitatieformulier
│   ├── admin_login.html           Inlogpagina beheerpaneel
│   ├── admin_dashboard.html       Overzicht + opdrachten online/offline zetten
│   ├── admin_opdracht_form.html   Formulier om een nieuwe opdracht te plaatsen
│   └── admin_sollicitaties.html   Sollicitaties + cv-downloads per opdracht
├── uploads/cvs/                   Geüploade cv's (niet publiek toegankelijk)
└── static/
    ├── style.css           Alle styling
    └── script.js           Mobiel menu, FAQ-accordion, max. 3 functies selecteren
```

## Voordat je live gaat

- Zet een sterk, uniek wachtwoord bij `ADMIN_PASSWORD` in `.env` — dit is de
  enige beveiliging van je beheerpaneel en de cv's van sollicitanten.
- Vervang de placeholder-gegevens in `templates/base.html` (telefoonnummer,
  KvK-nummer, adres) door je echte bedrijfsgegevens.
- Zet `FLASK_DEBUG=0` in `.env` zodra de site online staat, en gebruik een
  echte WSGI-server (bijv. `gunicorn app:app`) in plaats van `python app.py`.
- Stel een sterke, unieke `SECRET_KEY` in.
- Overweeg een spamfilter zoals een honeypot-veld of reCAPTCHA op de
  formulieren als je veel ongewenste inzendingen krijgt.
