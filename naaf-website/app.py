"""
Naaf — Flask-backend voor de website.

Verantwoordelijk voor:
- De homepage tonen (templates/index.html)
- Het intakeformulier voor bedrijven (opdracht plaatsen)
- Het aanmeldformulier voor ICT-professionals
- Beide aanvragen opslaan in een PostgreSQL-database (Supabase)
- Een e-mailnotificatie sturen naar het bedrijf zelf én een bevestiging
  naar de aanvrager, zodra e-mail (SMTP) is geconfigureerd via .env
"""

import json
import mimetypes
import os
import re
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB — ruim genoeg voor een cv of vacature-pdf

DATABASE_URL = os.environ.get("DATABASE_URL") or ""
CONTACT_EMAIL = os.environ.get("MAIL_TO", "hallo@naafdetachering.nl")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(app.root_path, "uploads", "cvs")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_CV_EXTENSIONS = {"pdf", "doc", "docx"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FUNCTIES = 3
SOLLICITATIE_STATUSSEN = ["in behandeling", "gaat door", "afgewezen"]

# Functietitels waaruit een sollicitant kan kiezen (max 3) en waaruit een
# opdracht een hoofdcategorie krijgt bij het plaatsen ervan.
FUNCTIES = [
    "Adviseur", "Agile coach", "Architect", "Backend Ontwikkelaar", "BI Consultant",
    "Business Analist", "C#.Net Ontwikkelaar", "Change Manager", "CISO", "Cloud Architect",
    "Cloud Engineer", "Compliancy Officer", "Coördinator", "Data Analist", "Data Architect",
    "Data Engineer", "Datascientist", "DBA", "Delivery Manager", "DevOps engineer",
    "DevOps Linux", "DevOps Windows", "Digital Architect", "DIV / Archief", "Domain Architect",
    "DWH Ontwikkelaar", "Embedded", "Enterprise Architect", "ETL Ontwikkelaar",
    "Frontend Ontwikkelaar", "Full Stack Ontwikkelaar", "Functioneel Applicatiebeheerder",
    "Helpdesk", "IB adviseur", "Informatie Analist", "Infra Architect",
    "Integration Architect", "ISO", "IT Architect", "IT Manager", "Java Ontwikkelaar",
    "Netwerk Architect", "Netwerkbeheerder", "Oracle Ontwikkelaar", "PMO",
    "Portfolio Manager", "Principal Architect", "Privacy Officer", "Productowner",
    "Programmamanager", "Projectleider", "Projectmanager", "Python Ontwikkelaar",
    "Scrummaster", "Security Engineer", "Security Officer", "Service Manager",
    "Software Architect", "Solution Architect", "Systeembeheerder", "Teamleider",
    "Technisch Applicatiebeheerder", "Technisch helpdesk", "Tester", "Testmanager",
    "UX/UI designer", "Werkplekbeheerder",
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    _require_database_url()
    if "db" not in g:
        g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _require_database_url():
    if not DATABASE_URL:
        raise RuntimeError(
            "Er is nog geen DATABASE_URL ingesteld in je .env-bestand. "
            "Kopieer de connection string uit je Supabase-project (Project Settings > "
            "Database > Connection string > URI) en zet 'm in .env als DATABASE_URL=..."
        )


def init_db():
    """Create the tables if they don't exist yet. Safe to call on every startup."""
    _require_database_url()
    with psycopg.connect(DATABASE_URL) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bedrijf_aanvragen (
                id SERIAL PRIMARY KEY,
                aangemaakt_op TEXT NOT NULL,
                bedrijfsnaam TEXT NOT NULL,
                contactpersoon TEXT NOT NULL,
                email TEXT NOT NULL,
                telefoon TEXT,
                functie TEXT NOT NULL,
                startdatum TEXT,
                duur TEXT,
                toelichting TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS professional_aanmeldingen (
                id SERIAL PRIMARY KEY,
                aangemaakt_op TEXT NOT NULL,
                naam TEXT NOT NULL,
                email TEXT NOT NULL,
                telefoon TEXT,
                vakgebied TEXT NOT NULL,
                ervaring TEXT,
                beschikbaarheid TEXT,
                toelichting TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS opdrachten (
                id SERIAL PRIMARY KEY,
                aangemaakt_op TEXT NOT NULL,
                titel TEXT NOT NULL,
                functiegebied TEXT NOT NULL,
                locatie TEXT,
                uren TEXT,
                tarief_indicatie TEXT,
                startdatum TEXT,
                duur TEXT,
                samenvatting TEXT NOT NULL,
                omschrijving TEXT NOT NULL,
                eisen_json TEXT NOT NULL DEFAULT '[]',
                wensen_json TEXT NOT NULL DEFAULT '[]',
                actief BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sollicitaties (
                id SERIAL PRIMARY KEY,
                opdracht_id INTEGER NOT NULL REFERENCES opdrachten (id),
                aangemaakt_op TEXT NOT NULL,
                naam TEXT NOT NULL,
                email TEXT NOT NULL,
                telefoon TEXT,
                linkedin TEXT,
                functies_json TEXT NOT NULL DEFAULT '[]',
                cv_bestandsnaam TEXT NOT NULL,
                cv_originele_naam TEXT NOT NULL,
                motivatie TEXT NOT NULL,
                eis_antwoorden_json TEXT NOT NULL DEFAULT '[]',
                wens_antwoorden_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )

        # Onderstaande ADD COLUMN's zijn allemaal idempotent (IF NOT EXISTS) —
        # veilig om bij elke start opnieuw uit te voeren, ook op een database
        # die al bestaat vanuit een eerdere versie van de site.
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS linkedin TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS woonplaats TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS profielfoto_bestandsnaam TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS profielfoto_originele_naam TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS tags_json TEXT NOT NULL DEFAULT '[]'")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS voorkeur TEXT NOT NULL DEFAULT 'beide'")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS cv_bestandsnaam TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS cv_originele_naam TEXT")
        db.execute("ALTER TABLE professional_aanmeldingen ADD COLUMN IF NOT EXISTS wachtwoord_hash TEXT")

        db.execute("ALTER TABLE sollicitaties ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'in behandeling'")
        db.execute("ALTER TABLE sollicitaties ADD COLUMN IF NOT EXISTS volg_token TEXT")

        db.execute("ALTER TABLE opdrachten ADD COLUMN IF NOT EXISTS notities TEXT")
        db.execute("ALTER TABLE opdrachten ADD COLUMN IF NOT EXISTS sollicitatie_deadline TEXT")

        db.commit()


def seed_dummy_opdrachten():
    """Zet 3 voorbeeldopdrachten in de database, maar alleen als er nog geen
    opdrachten bestaan — zo overschrijft dit nooit echte, ingevoerde opdrachten."""
    _require_database_url()
    with psycopg.connect(DATABASE_URL) as db:
        count = db.execute("SELECT COUNT(*) FROM opdrachten").fetchone()[0]
        if count > 0:
            return

        nu = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        vandaag = datetime.now(timezone.utc).date()
        voorbeelden = [
            {
                "titel": "Testcoördinator Acceptatietesten",
                "functiegebied": "Testmanager",
                "locatie": "Utrecht (hybride)",
                "uren": "32-40 uur per week",
                "tarief_indicatie": "In overleg",
                "startdatum": "Zo snel mogelijk",
                "duur": "6 maanden, met optie tot verlenging",
                "sollicitatie_deadline": (vandaag + timedelta(days=3)).isoformat(),
                "samenvatting": "Coördineer de gebruikersacceptatietesten voor een bedrijfskritisch platform bij een opdrachtgever in de publieke sector.",
                "omschrijving": (
                    "Voor een opdrachtgever in de publieke sector zoeken we een ervaren testcoördinator "
                    "die de gebruikersacceptatietesten (UAT) rond een bedrijfskritisch systeem in goede "
                    "banen leidt.\n\n"
                    "Je stelt zelfstandig een risicogerichte teststrategie op, stemt af met meerdere "
                    "gebruikersgroepen en beheerders, en bewaakt de voortgang richting een zorgvuldige "
                    "livegang. Je werkt nauw samen met de projectleider en rapporteert rechtstreeks aan "
                    "de opdrachtgever."
                ),
                "eisen": [
                    "Je hebt minimaal vijf jaar aantoonbare ervaring met testcoördinatie, waarvan aantoonbare ervaring met het organiseren en coördineren van gebruikersacceptatietesten.",
                    "Je hebt aantoonbare ervaring met het zelfstandig opstellen en uitvoeren van een risicogerichte test- en acceptatieaanpak, inclusief scope, planning, prioritering, voortgang en rapportage.",
                    "Je hebt aantoonbare ervaring met het coördineren van testen waarbij meerdere gebruikersgroepen, beheerders en andere stakeholders betrokken zijn en waarbij de inhoudelijke testen grotendeels door deze partijen worden uitgevoerd.",
                ],
                "wensen": [
                    "Je beschikt over aantoonbare ervaring met bedrijfskritische systemen, continuïteitsrisico's en/of systemen met externe gebruikers of ontsluitingen.",
                    "Je beschikt over aantoonbare ervaring met het inventariseren en structureren van een nog niet volledig bekende scope, afhankelijkheden, stakeholders en risico's.",
                    "Je beschikt over aantoonbare ervaring met cloudmigraties, platformmigraties en/of datamigraties.",
                    "Je beschikt over aantoonbare ervaring met risicomanagement.",
                    "Je beschikt over aantoonbare ervaring met het opstellen en bewaken van testplanningen en het coördineren van meerdere parallelle testactiviteiten en gebruikersgroepen.",
                    "Je beschikt over aantoonbare ervaring met het begeleiden en ondersteunen van key users en inhoudelijke experts bij het voorbereiden en uitvoeren van gebruikersacceptatietesten.",
                ],
            },
            {
                "titel": "Cloud Engineer Azure-migratie",
                "functiegebied": "Cloud Engineer",
                "locatie": "Amsterdam (hybride)",
                "uren": "24-32 uur per week",
                "tarief_indicatie": "In overleg",
                "startdatum": "Per direct",
                "duur": "4 maanden",
                "sollicitatie_deadline": (vandaag + timedelta(days=6)).isoformat(),
                "samenvatting": "Begeleid de migratie van on-premise infrastructuur naar Azure bij een groeiende scale-up.",
                "omschrijving": (
                    "Een groeiende scale-up migreert de komende maanden haar on-premise omgeving naar "
                    "Azure. We zoeken een cloud engineer die deze migratie technisch trekt: van het "
                    "opzetten van de doelarchitectuur tot het daadwerkelijk overzetten van workloads, "
                    "met zo min mogelijk impact op de dagelijkse dienstverlening."
                ),
                "eisen": [
                    "Je hebt aantoonbare ervaring met het opzetten en migreren van infrastructuur naar Microsoft Azure.",
                    "Je hebt ervaring met infrastructure-as-code (bijvoorbeeld Terraform of Bicep) in een productieomgeving.",
                ],
                "wensen": [
                    "Je hebt ervaring met het migreren van omgevingen die tijdens de migratie in gebruik blijven.",
                    "Je hebt kennis van netwerkbeveiliging in een cloudomgeving.",
                ],
            },
            {
                "titel": "Frontend Ontwikkelaar React",
                "functiegebied": "Frontend Ontwikkelaar",
                "locatie": "Rotterdam (op locatie)",
                "uren": "32-40 uur per week",
                "tarief_indicatie": "In overleg",
                "startdatum": "1 november",
                "duur": "3 maanden, verlenging mogelijk",
                "sollicitatie_deadline": (vandaag + timedelta(days=21)).isoformat(),
                "samenvatting": "Bouw mee aan een nieuw klantportaal voor een financiële dienstverlener.",
                "omschrijving": (
                    "Voor een financiële dienstverlener bouwen we een nieuw klantportaal in React. "
                    "Je sluit aan bij een bestaand ontwikkelteam en werkt aan herbruikbare componenten, "
                    "toegankelijkheid en performance, in nauwe samenwerking met een UX-designer en "
                    "backend-ontwikkelaars."
                ),
                "eisen": [
                    "Je hebt minimaal drie jaar ervaring met het bouwen van productieklare applicaties in React.",
                    "Je hebt ervaring met het werken volgens toegankelijkheidsrichtlijnen (WCAG).",
                    "Je hebt ervaring met het samenwerken in een scrumteam.",
                ],
                "wensen": [],
            },
        ]

        for v in voorbeelden:
            db.execute(
                """
                INSERT INTO opdrachten
                    (aangemaakt_op, titel, functiegebied, locatie, uren, tarief_indicatie,
                     startdatum, duur, samenvatting, omschrijving, eisen_json, wensen_json, actief,
                     sollicitatie_deadline)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                """,
                (
                    nu,
                    v["titel"],
                    v["functiegebied"],
                    v["locatie"],
                    v["uren"],
                    v["tarief_indicatie"],
                    v["startdatum"],
                    v["duur"],
                    v["samenvatting"],
                    v["omschrijving"],
                    json.dumps(v["eisen"]),
                    json.dumps(v["wensen"]),
                    v["sollicitatie_deadline"],
                ),
            )
        db.commit()


# ---------------------------------------------------------------------------
# CSRF protection (lightweight, no extra dependency)
# ---------------------------------------------------------------------------

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(submitted, expected):
            abort(400, description="Ongeldige of verlopen formulierbeveiliging. Ververs de pagina en probeer opnieuw.")


# ---------------------------------------------------------------------------
# E-mail helper
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str, to_address: str, attachment_path: str | None = None, attachment_filename: str | None = None) -> bool:
    """Send a plain-text e-mail, optionally with one file attached. Returns True on success.

    Configure SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD (see .env.example).
    If SMTP isn't configured, the e-mail is skipped and logged — the form
    submission itself still succeeds and is saved to the database.
    """
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM", user)

    if not all([host, user, password, mail_from]):
        app.logger.warning("SMTP is niet (volledig) geconfigureerd — e-mail niet verstuurd: %r", subject)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = to_address
    message.set_content(body)

    if attachment_path and os.path.isfile(attachment_path):
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(attachment_path, "rb") as f:
            message.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment_filename or os.path.basename(attachment_path),
            )

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 — we log and continue, never crash the request
        app.logger.error("Versturen van e-mail naar %s is mislukt: %s", to_address, exc)
        return False


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def is_valid_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.match(value or ""))


def bereken_deadline_info(deadline_str):
    """Geeft None terug als er geen (geldige) deadline is, anders een dict met
    'dagen' (kan negatief zijn) en 'kleur' (rood/oranje/groen):
    - verstreken of nog 4 dagen of minder  -> rood
    - nog 5 t/m 7 dagen                    -> oranje
    - meer dan 7 dagen                     -> groen
    """
    if not deadline_str:
        return None
    try:
        deadline_datum = datetime.strptime(deadline_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    dagen = (deadline_datum - datetime.now(timezone.utc).date()).days
    if dagen <= 4:
        kleur = "rood"
    elif dagen <= 7:
        kleur = "oranje"
    else:
        kleur = "groen"
    return {"dagen": dagen, "kleur": kleur, "verstreken": dagen < 0}


def allowed_cv_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_CV_EXTENSIONS


def allowed_image_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# Admin authenticatie
# ---------------------------------------------------------------------------

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def professional_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("professional_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# AI-invulhulp: vacaturetekst (geplakt of als pdf) omzetten naar opdrachtvelden
# ---------------------------------------------------------------------------

class AIExtractError(Exception):
    """Duidelijke, aan de gebruiker te tonen fout bij het AI-invullen."""


def extract_text_from_pdf(file_storage) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise AIExtractError(
            "De pypdf-bibliotheek ontbreekt. Installeer 'm met: pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(file_storage)
        tekst = "\n\n".join((pagina.extract_text() or "") for pagina in reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise AIExtractError("Kon de pdf niet lezen. Is het bestand niet beschadigd?") from exc

    if not tekst.strip():
        raise AIExtractError(
            "Er kon geen tekst uit deze pdf gehaald worden — mogelijk is het een ingescande "
            "afbeelding zonder doorzoekbare tekst. Plak de tekst dan handmatig in het tekstveld."
        )
    return tekst


def ai_extract_opdracht(vacaturetekst: str) -> dict:
    """Stuurt vacaturetekst naar Groq (gratis, geen creditcard nodig) en geeft een dict
    terug met opdrachtvelden.

    Vult bewust GEEN tarief in — dat blijft altijd een handmatige, bewuste keuze.
    """
    if not GROQ_API_KEY:
        raise AIExtractError(
            "Er is nog geen GROQ_API_KEY ingesteld in je .env-bestand. "
            "Maak gratis een key aan via console.groq.com en zet 'm in .env."
        )

    try:
        from groq import Groq
    except ImportError as exc:
        raise AIExtractError(
            "De groq-bibliotheek ontbreekt. Installeer 'm met: pip install -r requirements.txt"
        ) from exc

    functielijst = "\n".join(f"- {f}" for f in FUNCTIES)

    prompt = f"""Je krijgt hieronder de tekst van een ICT-vacature/opdracht (bijvoorbeeld van een
inhuurplatform zoals Flextender, of rechtstreeks van een bedrijf). Haal hier de gegevens uit
en geef ZE UITSLUITEND terug als geldig JSON-object, zonder uitleg, zonder markdown-codeblok,
precies met deze sleutels:

{{
  "titel": "korte functietitel, bijv. Functioneel Beheerder Corsa",
  "functiegebied": "kies het EEN BESTE passende item, EXACT overgenomen, uit deze lijst: {functielijst}",
  "locatie": "regio/plaats, voeg (hybride) toe als hybride werken mogelijk is, anders (op locatie)",
  "uren": "bijv. '36 uur per week'",
  "startdatum": "bijv. '1 oktober 2026', of 'Zo snel mogelijk' als dat niet concreet vermeld staat",
  "duur": "bijv. '3 maanden, geen verlenging' — leid dit af uit start- en einddatum indien nodig",
  "samenvatting": "één pakkende zin (max. ~20 woorden) die de opdracht samenvat voor een overzichtspagina",
  "omschrijving": "een lopende, natuurlijke omschrijving van 2-4 alinea's in het Nederlands, gescheiden door een lege regel (\\n\\n), gebaseerd op de opdrachtomschrijving en het beoogde resultaat uit de brontekst",
  "eisen": ["array van strings — ALLEEN de harde, kandidaat-gerichte knock-outeisen (ervaring, opleiding, beschikbaarheid). Laat eisen over het MAXIMUMTARIEF of de fee van een platform WEG, die vult de gebruiker zelf in."],
  "wensen": ["array van strings — de kandidaat-gerichte gunningscriteria/wensen die GEEN eis zijn. Laat ook hier tarief-gerelateerde criteria WEG. Geef een lege array [] als er geen wensen zijn."]
}}

Regels:
- Geef ALLEEN het JSON-object terug, niets ervoor of erna.
- Schrijf de omschrijving in eigen, natuurlijke bewoording — kopieer de brontekst niet woordelijk over.
- Als een veld niet met zekerheid uit de tekst valt af te leiden, geef dan een lege string "" (of [] voor eisen/wensen) — verzin niets.

Brontekst:
---
{vacaturetekst[:12000]}
---
"""

    client = Groq(api_key=GROQ_API_KEY)
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=2000,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Groq API-aanroep mislukt: %s", exc)
        raise AIExtractError(f"Aanroep naar Groq is mislukt: {exc}") from exc

    ruwe_tekst = (completion.choices[0].message.content or "").strip()

    # Voor de zekerheid eventuele ```json ... ``` codeblok-markering verwijderen
    if ruwe_tekst.startswith("```"):
        ruwe_tekst = re.sub(r"^```(?:json)?\s*", "", ruwe_tekst)
        ruwe_tekst = re.sub(r"\s*```$", "", ruwe_tekst)

    try:
        data = json.loads(ruwe_tekst)
    except json.JSONDecodeError as exc:
        app.logger.error("Kon AI-antwoord niet als JSON lezen: %s\nAntwoord was: %s", exc, ruwe_tekst)
        raise AIExtractError(
            "Groq gaf een antwoord terug dat niet als JSON te lezen was. Probeer het opnieuw, "
            "of vul het formulier handmatig in."
        ) from exc

    # Nette, voorspelbare structuur garanderen, ook als het model een sleutel oversloeg
    return {
        "titel": str(data.get("titel", "") or ""),
        "functiegebied": str(data.get("functiegebied", "") or ""),
        "locatie": str(data.get("locatie", "") or ""),
        "uren": str(data.get("uren", "") or ""),
        "startdatum": str(data.get("startdatum", "") or ""),
        "duur": str(data.get("duur", "") or ""),
        "samenvatting": str(data.get("samenvatting", "") or ""),
        "omschrijving": str(data.get("omschrijving", "") or ""),
        "eisen": [str(e) for e in (data.get("eisen") or []) if str(e).strip()],
        "wensen": [str(w) for w in (data.get("wensen") or []) if str(w).strip()],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", contact_email=CONTACT_EMAIL)


@app.route("/contact/bedrijf", methods=["POST"])
def contact_bedrijf():
    bedrijfsnaam = request.form.get("bedrijfsnaam", "").strip()
    contactpersoon = request.form.get("contactpersoon", "").strip()
    email = request.form.get("email", "").strip()
    telefoon = request.form.get("telefoon", "").strip()
    functie = request.form.get("functie", "").strip()
    startdatum = request.form.get("startdatum", "").strip()
    duur = request.form.get("duur", "").strip()
    toelichting = request.form.get("toelichting", "").strip()

    if not all([bedrijfsnaam, contactpersoon, email, functie]):
        flash("Vul in elk geval bedrijfsnaam, contactpersoon, e-mail en de gezochte rol in.", "bedrijf-error")
        return redirect(url_for("index") + "#bedrijven")

    if not is_valid_email(email):
        flash("Dat e-mailadres lijkt niet te kloppen. Controleer het en probeer opnieuw.", "bedrijf-error")
        return redirect(url_for("index") + "#bedrijven")

    db = get_db()
    db.execute(
        """
        INSERT INTO bedrijf_aanvragen
            (aangemaakt_op, bedrijfsnaam, contactpersoon, email, telefoon, functie, startdatum, duur, toelichting)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
            bedrijfsnaam,
            contactpersoon,
            email,
            telefoon,
            functie,
            startdatum,
            duur,
            toelichting,
        ),
    )
    db.commit()

    notify_body = (
        "Nieuwe opdracht-aanvraag via naaf.nl\n\n"
        f"Bedrijf: {bedrijfsnaam}\n"
        f"Contactpersoon: {contactpersoon}\n"
        f"E-mail: {email}\n"
        f"Telefoon: {telefoon or '-'}\n"
        f"Gezochte rol/opdracht: {functie}\n"
        f"Gewenste startdatum: {startdatum or '-'}\n"
        f"Verwachte duur: {duur or '-'}\n\n"
        f"Toelichting:\n{toelichting or '-'}\n"
    )
    send_email(f"Nieuwe opdracht-aanvraag: {bedrijfsnaam}", notify_body, CONTACT_EMAIL)

    confirmation_body = (
        f"Hoi {contactpersoon},\n\n"
        f"Bedankt voor je aanvraag voor '{functie}'. We hebben ontvangen "
        "wat je hebt ingevuld en nemen persoonlijk contact met je op zodra "
        "we een passende professional voor je hebben gevonden.\n\n"
        "Heb je in de tussentijd een vraag? Antwoord gerust op deze e-mail.\n\n"
        "Met vriendelijke groet,\nTeam Naaf"
    )
    send_email("We hebben je aanvraag ontvangen — Naaf", confirmation_body, email)

    flash("Bedankt! We hebben je aanvraag ontvangen en nemen persoonlijk contact met je op.", "bedrijf-success")
    return redirect(url_for("index") + "#bedrijven")


@app.route("/contact/professional", methods=["POST"])
def contact_professional():
    naam = request.form.get("naam", "").strip()
    email = request.form.get("email", "").strip()
    telefoon = request.form.get("telefoon", "").strip()
    linkedin = request.form.get("linkedin", "").strip()
    woonplaats = request.form.get("woonplaats", "").strip()
    vakgebied = request.form.get("vakgebied", "").strip()
    ervaring = request.form.get("ervaring", "").strip()
    beschikbaarheid = request.form.get("beschikbaarheid", "").strip()
    toelichting = request.form.get("toelichting", "").strip()
    voorkeur = request.form.get("voorkeur", "beide").strip()
    if voorkeur not in ("zzp", "detachering", "beide"):
        voorkeur = "beide"

    tags_ruw = request.form.get("tags", "").strip()
    tags = [t.strip() for t in tags_ruw.split(",") if t.strip()][:30]

    cv_bestand = request.files.get("cv")
    foto_bestand = request.files.get("profielfoto")

    wachtwoord = request.form.get("wachtwoord", "")
    wachtwoord_bevestig = request.form.get("wachtwoord_bevestig", "")

    fouten = []
    if not naam:
        fouten.append("naam")
    if not email or not is_valid_email(email):
        fouten.append("een geldig e-mailadres")
    if not vakgebied:
        fouten.append("vakgebied")
    if not cv_bestand or cv_bestand.filename == "":
        fouten.append("een cv")
    elif not allowed_cv_file(cv_bestand.filename):
        flash("Upload je cv als PDF, DOC of DOCX.", "professional-error")
        return redirect(url_for("index") + "#professionals")

    if foto_bestand and foto_bestand.filename and not allowed_image_file(foto_bestand.filename):
        flash("Upload je profielfoto als JPG, PNG of WEBP.", "professional-error")
        return redirect(url_for("index") + "#professionals")

    if len(wachtwoord) < 8:
        fouten.append("een wachtwoord van minimaal 8 tekens")
    elif wachtwoord != wachtwoord_bevestig:
        flash("De wachtwoorden komen niet overeen. Probeer het opnieuw.", "professional-error")
        return redirect(url_for("index") + "#professionals")

    if fouten:
        flash(f"Vul in elk geval het volgende in: {', '.join(fouten)}.", "professional-error")
        return redirect(url_for("index") + "#professionals")

    db = get_db()
    bestaand = db.execute(
        "SELECT id FROM professional_aanmeldingen WHERE email = %s", (email,)
    ).fetchone()
    if bestaand:
        flash(
            "Dit e-mailadres is al bekend bij ons. Log in om je profiel te beheren, "
            "in plaats van je opnieuw aan te melden.",
            "professional-error",
        )
        return redirect(url_for("admin_login"))

    cv_originele_naam = secure_filename(cv_bestand.filename)
    cv_extensie = cv_originele_naam.rsplit(".", 1)[1].lower()
    cv_opgeslagen_naam = f"{uuid.uuid4().hex}.{cv_extensie}"
    cv_bestand.save(os.path.join(UPLOAD_FOLDER, cv_opgeslagen_naam))

    foto_opgeslagen_naam = None
    foto_originele_naam = None
    if foto_bestand and foto_bestand.filename:
        foto_originele_naam = secure_filename(foto_bestand.filename)
        foto_extensie = foto_originele_naam.rsplit(".", 1)[1].lower()
        foto_opgeslagen_naam = f"{uuid.uuid4().hex}.{foto_extensie}"
        foto_bestand.save(os.path.join(UPLOAD_FOLDER, foto_opgeslagen_naam))

    db.execute(
        """
        INSERT INTO professional_aanmeldingen
            (aangemaakt_op, naam, email, telefoon, linkedin, woonplaats, vakgebied, ervaring,
             beschikbaarheid, toelichting, voorkeur, tags_json, cv_bestandsnaam, cv_originele_naam,
             profielfoto_bestandsnaam, profielfoto_originele_naam, wachtwoord_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
            naam, email, telefoon, linkedin, woonplaats, vakgebied, ervaring,
            beschikbaarheid, toelichting, voorkeur, json.dumps(tags),
            cv_opgeslagen_naam, cv_originele_naam, foto_opgeslagen_naam, foto_originele_naam,
            generate_password_hash(wachtwoord),
        ),
    )
    db.commit()

    notify_body = (
        "Nieuwe aanmelding van een ICT-professional via naafdetachering.nl\n\n"
        f"Naam: {naam}\n"
        f"E-mail: {email}\n"
        f"Telefoon: {telefoon or '-'}\n"
        f"LinkedIn: {linkedin or '-'}\n"
        f"Woonplaats: {woonplaats or '-'}\n"
        f"Vakgebied: {vakgebied}\n"
        f"Voorkeur: {voorkeur}\n"
        f"Tags: {', '.join(tags) if tags else '-'}\n"
        f"Ervaring: {ervaring or '-'}\n"
        f"Beschikbaar vanaf: {beschikbaarheid or '-'}\n\n"
        f"Toelichting:\n{toelichting or '-'}\n"
    )
    send_email(
        f"Nieuwe aanmelding professional: {naam}", notify_body, CONTACT_EMAIL,
        attachment_path=os.path.join(UPLOAD_FOLDER, cv_opgeslagen_naam),
        attachment_filename=cv_originele_naam,
    )

    confirmation_body = (
        f"Hoi {naam},\n\n"
        "Bedankt voor je aanmelding bij Naaf. We hebben je gegevens ontvangen "
        "en nemen persoonlijk contact met je op zodra er een opdracht is die "
        "bij je past.\n\n"
        "Met vriendelijke groet,\nTeam Naaf"
    )
    send_email("We hebben je aanmelding ontvangen — Naaf", confirmation_body, email)

    flash("Bedankt voor je aanmelding! We nemen persoonlijk contact met je op zodra er een passende opdracht is.", "professional-success")
    return redirect(url_for("index") + "#professionals")


@app.route("/opdrachten")
def opdrachten_lijst():
    db = get_db()
    rijen = db.execute(
        "SELECT * FROM opdrachten WHERE actief ORDER BY id DESC"
    ).fetchall()
    opdrachten_verwerkt = []
    for o in rijen:
        o = dict(o)
        o["deadline_info"] = bereken_deadline_info(o.get("sollicitatie_deadline"))
        opdrachten_verwerkt.append(o)
    return render_template("opdrachten.html", opdrachten=opdrachten_verwerkt, contact_email=CONTACT_EMAIL)


@app.route("/opdrachten/<int:opdracht_id>")
def opdracht_detail(opdracht_id):
    db = get_db()
    opdracht = db.execute(
        "SELECT * FROM opdrachten WHERE id = %s AND actief", (opdracht_id,)
    ).fetchone()
    if opdracht is None:
        abort(404)

    eisen = json.loads(opdracht["eisen_json"])
    wensen = json.loads(opdracht["wensen_json"])
    deadline_info = bereken_deadline_info(opdracht["sollicitatie_deadline"])
    return render_template(
        "opdracht_detail.html",
        opdracht=opdracht,
        eisen=eisen,
        wensen=wensen,
        deadline_info=deadline_info,
        functies=FUNCTIES,
        max_functies=MAX_FUNCTIES,
        contact_email=CONTACT_EMAIL,
    )


@app.route("/opdrachten/<int:opdracht_id>/solliciteer", methods=["POST"])
def opdracht_solliciteren(opdracht_id):
    db = get_db()
    opdracht = db.execute(
        "SELECT * FROM opdrachten WHERE id = %s AND actief", (opdracht_id,)
    ).fetchone()
    if opdracht is None:
        abort(404)

    detail_url = url_for("opdracht_detail", opdracht_id=opdracht_id)

    if opdracht["sollicitatie_deadline"]:
        try:
            deadline_datum = datetime.strptime(opdracht["sollicitatie_deadline"], "%Y-%m-%d").date()
            if datetime.now(timezone.utc).date() > deadline_datum:
                flash("De sollicitatietermijn voor deze opdracht is helaas verstreken.", "sollicitatie-error")
                return redirect(detail_url + "#solliciteren")
        except ValueError:
            pass

    naam = request.form.get("naam", "").strip()
    email = request.form.get("email", "").strip()
    telefoon = request.form.get("telefoon", "").strip()
    linkedin = request.form.get("linkedin", "").strip()
    functies = [f for f in request.form.getlist("functies") if f in FUNCTIES][:MAX_FUNCTIES]
    motivatie = request.form.get("motivatie", "").strip()
    cv_bestand = request.files.get("cv")

    eisen = json.loads(opdracht["eisen_json"])
    wensen = json.loads(opdracht["wensen_json"])
    eis_antwoorden = [
        {"vraag": eis, "antwoord": request.form.get(f"eis_{i}", "").strip()}
        for i, eis in enumerate(eisen)
    ]
    wens_antwoorden = [
        {"vraag": wens, "antwoord": request.form.get(f"wens_{i}", "").strip()}
        for i, wens in enumerate(wensen)
    ]

    fouten = []
    if not naam:
        fouten.append("naam")
    if not email or not is_valid_email(email):
        fouten.append("een geldig e-mailadres")
    if not motivatie:
        fouten.append("motivatie")
    if not cv_bestand or cv_bestand.filename == "":
        fouten.append("een cv")
    elif not allowed_cv_file(cv_bestand.filename):
        flash("Upload je cv als PDF, DOC of DOCX.", "sollicitatie-error")
        return redirect(detail_url + "#solliciteren")
    if any(not a["antwoord"] for a in eis_antwoorden):
        fouten.append("een toelichting bij elke eis")

    if fouten:
        flash(f"Vul in elk geval het volgende in: {', '.join(fouten)}.", "sollicitatie-error")
        return redirect(detail_url + "#solliciteren")

    # Cv veilig opslaan onder een unieke bestandsnaam, zodat gelijknamige
    # bestanden van verschillende sollicitanten elkaar niet overschrijven.
    originele_naam = secure_filename(cv_bestand.filename)
    extensie = originele_naam.rsplit(".", 1)[1].lower()
    opgeslagen_naam = f"{uuid.uuid4().hex}.{extensie}"
    opgeslagen_pad = os.path.join(UPLOAD_FOLDER, opgeslagen_naam)
    cv_bestand.save(opgeslagen_pad)

    volg_token = uuid.uuid4().hex

    db.execute(
        """
        INSERT INTO sollicitaties
            (opdracht_id, aangemaakt_op, naam, email, telefoon, linkedin, functies_json,
             cv_bestandsnaam, cv_originele_naam, motivatie, eis_antwoorden_json, wens_antwoorden_json,
             status, volg_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            opdracht_id,
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
            naam,
            email,
            telefoon,
            linkedin,
            json.dumps(functies),
            opgeslagen_naam,
            originele_naam,
            motivatie,
            json.dumps(eis_antwoorden),
            json.dumps(wens_antwoorden),
            "in behandeling",
            volg_token,
        ),
    )
    db.commit()

    notify_lines = [
        f"Nieuwe sollicitatie via naaf.nl op: {opdracht['titel']}",
        "",
        f"Naam: {naam}",
        f"E-mail: {email}",
        f"Telefoon: {telefoon or '-'}",
        f"LinkedIn: {linkedin or '-'}",
        f"Functie(s): {', '.join(functies) if functies else '-'}",
        "",
        f"Motivatie:\n{motivatie}",
        "",
    ]
    if eis_antwoorden:
        notify_lines.append("Antwoorden op de eisen:")
        for a in eis_antwoorden:
            notify_lines.append(f"- {a['vraag']}\n  {a['antwoord']}")
        notify_lines.append("")
    if wens_antwoorden:
        notify_lines.append("Antwoorden op de wensen:")
        for a in wens_antwoorden:
            if a["antwoord"]:
                notify_lines.append(f"- {a['vraag']}\n  {a['antwoord']}")
        notify_lines.append("")
    notify_lines.append("Het cv is als bijlage toegevoegd (of terug te vinden in het beheerpaneel).")

    send_email(
        f"Nieuwe sollicitatie: {naam} — {opdracht['titel']}",
        "\n".join(notify_lines),
        CONTACT_EMAIL,
        attachment_path=opgeslagen_pad,
        attachment_filename=originele_naam,
    )

    volg_url = url_for("sollicitatie_status", token=volg_token, _external=True)
    send_email(
        f"We hebben je sollicitatie ontvangen — {opdracht['titel']}",
        (
            f"Hoi {naam},\n\n"
            f"Bedankt voor je sollicitatie op '{opdracht['titel']}'. We hebben je gegevens, "
            "motivatie en cv in goede orde ontvangen en nemen persoonlijk contact met je op "
            "zodra we je sollicitatie hebben bekeken.\n\n"
            f"Je kunt de status van je sollicitatie hier altijd terugvinden:\n{volg_url}\n\n"
            "Met vriendelijke groet,\nTeam Naaf"
        ),
        email,
    )

    flash("Bedankt voor je sollicitatie! We hebben 'm ontvangen en nemen persoonlijk contact met je op.", "sollicitatie-success")
    return redirect(detail_url + "#solliciteren")


# ---------------------------------------------------------------------------
# Beheerpaneel — hier plaats jij zelf de opdrachten die je van bedrijven krijgt
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        wachtwoord = request.form.get("wachtwoord", "")

        # Admin: e-mailveld moet exact ADMIN_EMAIL zijn, wachtwoord exact ADMIN_PASSWORD
        if ADMIN_PASSWORD and email.lower() == ADMIN_EMAIL.lower() and secrets.compare_digest(wachtwoord, ADMIN_PASSWORD):
            session["is_admin"] = True
            session.pop("professional_id", None)
            bestemming = request.args.get("next") or url_for("admin_account")
            return redirect(bestemming)

        # Anders: proberen als professional-account
        if email and wachtwoord:
            db = get_db()
            professional = db.execute(
                "SELECT * FROM professional_aanmeldingen WHERE email = %s", (email,)
            ).fetchone()
            if professional and professional["wachtwoord_hash"] and check_password_hash(professional["wachtwoord_hash"], wachtwoord):
                session["professional_id"] = professional["id"]
                session.pop("is_admin", None)
                bestemming = request.args.get("next") or url_for("professional_dashboard")
                return redirect(bestemming)

        flash("E-mailadres of wachtwoord onjuist.", "admin-error")
        return redirect(url_for("admin_login"))

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    session.pop("professional_id", None)
    return redirect(url_for("index"))


@app.route("/admin/opdrachten")
@admin_required
def admin_dashboard():
    db = get_db()

    functiegebied_filter = request.args.get("functiegebied", "").strip()
    status_filter = request.args.get("status", "").strip()
    deadline_filter = request.args.get("deadline", "").strip()

    voorwaarden = []
    params = []
    if functiegebied_filter:
        voorwaarden.append("o.functiegebied = %s")
        params.append(functiegebied_filter)
    if status_filter == "open":
        voorwaarden.append("o.actief")
    elif status_filter == "offline":
        voorwaarden.append("NOT o.actief")

    where_clause = f"WHERE {' AND '.join(voorwaarden)}" if voorwaarden else ""
    opdrachten = db.execute(
        f"""
        SELECT o.*, (SELECT COUNT(*) FROM sollicitaties s WHERE s.opdracht_id = o.id) AS aantal_sollicitaties
        FROM opdrachten o
        {where_clause}
        ORDER BY o.id DESC
        """,
        params,
    ).fetchall()

    opdrachten_verwerkt = []
    for o in opdrachten:
        o = dict(o)
        o["deadline_info"] = bereken_deadline_info(o.get("sollicitatie_deadline"))
        opdrachten_verwerkt.append(o)

    # Deadline-filter doen we in Python, want die is afgeleid (rood/oranje/groen/geen)
    if deadline_filter == "urgent":
        opdrachten_verwerkt = [o for o in opdrachten_verwerkt if o["deadline_info"] and o["deadline_info"]["kleur"] in ("rood", "oranje")]
    elif deadline_filter == "ruim":
        opdrachten_verwerkt = [o for o in opdrachten_verwerkt if o["deadline_info"] and o["deadline_info"]["kleur"] == "groen"]
    elif deadline_filter == "geen":
        opdrachten_verwerkt = [o for o in opdrachten_verwerkt if not o["deadline_info"]]

    functiegebieden = [r["functiegebied"] for r in db.execute(
        "SELECT DISTINCT functiegebied FROM opdrachten ORDER BY functiegebied"
    ).fetchall()]

    return render_template(
        "admin_dashboard.html",
        opdrachten=opdrachten_verwerkt,
        functiegebieden=functiegebieden,
        actieve_functiegebied=functiegebied_filter,
        actieve_status=status_filter,
        actieve_deadline=deadline_filter,
    )


@app.route("/admin/account")
@admin_required
def admin_account():
    db = get_db()

    alle_opdrachten = db.execute("SELECT actief, sollicitatie_deadline FROM opdrachten").fetchall()
    totaal_open = sum(1 for o in alle_opdrachten if o["actief"])
    deadlines_bijna = sum(
        1 for o in alle_opdrachten
        if o["actief"] and bereken_deadline_info(o["sollicitatie_deadline"]) and bereken_deadline_info(o["sollicitatie_deadline"])["kleur"] in ("rood", "oranje")
    )
    totaal_in_behandeling = db.execute(
        "SELECT COUNT(*) AS n FROM sollicitaties WHERE status = 'in behandeling'"
    ).fetchone()["n"]
    week_geleden = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).isoformat(timespec="seconds")
    nieuwe_professionals = db.execute(
        "SELECT COUNT(*) AS n FROM professional_aanmeldingen WHERE aangemaakt_op >= %s", (week_geleden,)
    ).fetchone()["n"]

    return render_template(
        "admin_account.html",
        totaal_open=totaal_open,
        totaal_in_behandeling=totaal_in_behandeling,
        nieuwe_professionals=nieuwe_professionals,
        deadlines_bijna=deadlines_bijna,
    )


@app.route("/admin/opdrachten/nieuw", methods=["GET", "POST"])
@admin_required
def admin_opdracht_nieuw():
    if request.method == "POST":
        titel = request.form.get("titel", "").strip()
        functiegebied = request.form.get("functiegebied", "").strip()
        locatie = request.form.get("locatie", "").strip()
        uren = request.form.get("uren", "").strip()
        tarief_indicatie = request.form.get("tarief_indicatie", "").strip()
        startdatum = request.form.get("startdatum", "").strip()
        duur = request.form.get("duur", "").strip()
        samenvatting = request.form.get("samenvatting", "").strip()
        omschrijving = request.form.get("omschrijving", "").strip()
        notities = request.form.get("notities", "").strip()
        sollicitatie_deadline = request.form.get("sollicitatie_deadline", "").strip()
        eisen_ruw = request.form.get("eisen", "")
        wensen_ruw = request.form.get("wensen", "")

        eisen = [regel.strip() for regel in eisen_ruw.splitlines() if regel.strip()]
        wensen = [regel.strip() for regel in wensen_ruw.splitlines() if regel.strip()]

        if not all([titel, functiegebied, samenvatting, omschrijving]) or not eisen:
            flash("Vul in elk geval titel, functiegebied, samenvatting, omschrijving en minimaal één eis in.", "admin-error")
            return redirect(url_for("admin_opdracht_nieuw"))

        db = get_db()
        db.execute(
            """
            INSERT INTO opdrachten
                (aangemaakt_op, titel, functiegebied, locatie, uren, tarief_indicatie,
                 startdatum, duur, samenvatting, omschrijving, eisen_json, wensen_json, actief,
                 notities, sollicitatie_deadline)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
            """,
            (
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                titel, functiegebied, locatie, uren, tarief_indicatie,
                startdatum, duur, samenvatting, omschrijving,
                json.dumps(eisen), json.dumps(wensen), notities, sollicitatie_deadline,
            ),
        )
        db.commit()
        flash(f"Opdracht '{titel}' is geplaatst.", "admin-success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_opdracht_form.html", functies=FUNCTIES, voorinvulling=None, opdracht=None, modus="nieuw")


@app.route("/admin/opdrachten/<int:opdracht_id>/wijzigen", methods=["GET", "POST"])
@admin_required
def admin_opdracht_wijzigen(opdracht_id):
    db = get_db()
    opdracht = db.execute("SELECT * FROM opdrachten WHERE id = %s", (opdracht_id,)).fetchone()
    if opdracht is None:
        abort(404)

    if request.method == "POST":
        titel = request.form.get("titel", "").strip()
        functiegebied = request.form.get("functiegebied", "").strip()
        locatie = request.form.get("locatie", "").strip()
        uren = request.form.get("uren", "").strip()
        tarief_indicatie = request.form.get("tarief_indicatie", "").strip()
        startdatum = request.form.get("startdatum", "").strip()
        duur = request.form.get("duur", "").strip()
        samenvatting = request.form.get("samenvatting", "").strip()
        omschrijving = request.form.get("omschrijving", "").strip()
        notities = request.form.get("notities", "").strip()
        sollicitatie_deadline = request.form.get("sollicitatie_deadline", "").strip()
        eisen_ruw = request.form.get("eisen", "")
        wensen_ruw = request.form.get("wensen", "")

        eisen = [regel.strip() for regel in eisen_ruw.splitlines() if regel.strip()]
        wensen = [regel.strip() for regel in wensen_ruw.splitlines() if regel.strip()]

        if not all([titel, functiegebied, samenvatting, omschrijving]) or not eisen:
            flash("Vul in elk geval titel, functiegebied, samenvatting, omschrijving en minimaal één eis in.", "admin-error")
            return redirect(url_for("admin_opdracht_wijzigen", opdracht_id=opdracht_id))

        db.execute(
            """
            UPDATE opdrachten SET
                titel = %s, functiegebied = %s, locatie = %s, uren = %s, tarief_indicatie = %s,
                startdatum = %s, duur = %s, samenvatting = %s, omschrijving = %s,
                eisen_json = %s, wensen_json = %s, notities = %s, sollicitatie_deadline = %s
            WHERE id = %s
            """,
            (
                titel, functiegebied, locatie, uren, tarief_indicatie,
                startdatum, duur, samenvatting, omschrijving,
                json.dumps(eisen), json.dumps(wensen), notities, sollicitatie_deadline, opdracht_id,
            ),
        )
        db.commit()
        flash(f"Opdracht '{titel}' is bijgewerkt.", "admin-success")
        return redirect(url_for("admin_dashboard"))

    voorinvulling = {
        **dict(opdracht),
        "eisen": json.loads(opdracht["eisen_json"]),
        "wensen": json.loads(opdracht["wensen_json"]),
    }
    return render_template(
        "admin_opdracht_form.html", functies=FUNCTIES, voorinvulling=voorinvulling,
        opdracht=opdracht, modus="wijzigen",
    )


@app.route("/admin/opdrachten/nieuw/ai-invullen", methods=["POST"])
@admin_required
def admin_opdracht_ai_invullen():
    vacaturetekst = request.form.get("vacaturetekst", "").strip()
    pdf_bestand = request.files.get("vacature_pdf")

    try:
        if pdf_bestand and pdf_bestand.filename:
            if not pdf_bestand.filename.lower().endswith(".pdf"):
                raise AIExtractError("Upload alleen een PDF-bestand, of plak de tekst hieronder.")
            vacaturetekst = extract_text_from_pdf(pdf_bestand)

        if not vacaturetekst:
            raise AIExtractError("Plak vacaturetekst in het tekstveld, of upload een pdf.")

        voorinvulling = ai_extract_opdracht(vacaturetekst)
    except AIExtractError as exc:
        flash(str(exc), "admin-error")
        return render_template("admin_opdracht_form.html", functies=FUNCTIES, voorinvulling=None, opdracht=None, modus="nieuw")

    flash(
        "De velden zijn automatisch ingevuld op basis van de vacaturetekst — controleer alles, "
        "en vul zelf nog het tarief in voordat je de opdracht plaatst.",
        "admin-success",
    )
    return render_template("admin_opdracht_form.html", functies=FUNCTIES, voorinvulling=voorinvulling, opdracht=None, modus="nieuw")


@app.route("/admin/opdrachten/<int:opdracht_id>/toggle", methods=["POST"])
@admin_required
def admin_opdracht_toggle(opdracht_id):
    db = get_db()
    db.execute("UPDATE opdrachten SET actief = NOT actief WHERE id = %s", (opdracht_id,))
    db.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/aanvragen")
@admin_required
def admin_aanvragen():
    db = get_db()
    aanvragen = db.execute(
        "SELECT * FROM bedrijf_aanvragen ORDER BY id DESC"
    ).fetchall()
    return render_template("admin_aanvragen.html", aanvragen=aanvragen)


@app.route("/admin/aanvragen/<int:aanvraag_id>/verwijderen", methods=["POST"])
@admin_required
def admin_aanvraag_verwijderen(aanvraag_id):
    db = get_db()
    db.execute("DELETE FROM bedrijf_aanvragen WHERE id = %s", (aanvraag_id,))
    db.commit()
    flash("Aanvraag verwijderd.", "admin-success")
    return redirect(url_for("admin_aanvragen"))


@app.route("/admin/professionals")
@admin_required
def admin_professionals():
    db = get_db()
    vakgebied_filter = request.args.get("vakgebied", "").strip()

    if vakgebied_filter:
        professionals = db.execute(
            "SELECT * FROM professional_aanmeldingen WHERE vakgebied = %s ORDER BY id DESC",
            (vakgebied_filter,),
        ).fetchall()
    else:
        professionals = db.execute(
            "SELECT * FROM professional_aanmeldingen ORDER BY id DESC"
        ).fetchall()

    aanwezige_vakgebieden = db.execute(
        "SELECT DISTINCT vakgebied FROM professional_aanmeldingen ORDER BY vakgebied"
    ).fetchall()
    vakgebieden = [r["vakgebied"] for r in aanwezige_vakgebieden]

    verwerkt = []
    for p in professionals:
        verwerkt.append({**dict(p), "tags": json.loads(p["tags_json"] or "[]")})

    return render_template(
        "admin_professionals.html",
        professionals=verwerkt,
        vakgebieden=vakgebieden,
        actieve_filter=vakgebied_filter,
    )


@app.route("/admin/professionals/<int:professional_id>")
@admin_required
def admin_professional_detail(professional_id):
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (professional_id,)
    ).fetchone()
    if professional is None:
        abort(404)

    sollicitaties = db.execute(
        """
        SELECT s.*, o.titel AS opdracht_titel, o.id AS opdracht_id
        FROM sollicitaties s
        JOIN opdrachten o ON o.id = s.opdracht_id
        WHERE s.email = %s
        ORDER BY s.id DESC
        """,
        (professional["email"],),
    ).fetchall()

    professional_weergave = {**dict(professional), "tags": json.loads(professional["tags_json"] or "[]")}

    return render_template(
        "admin_professional_detail.html",
        professional=professional_weergave,
        sollicitaties=sollicitaties,
    )


@app.route("/admin/professionals/<int:professional_id>/verwijderen", methods=["POST"])
@admin_required
def admin_professional_verwijderen(professional_id):
    db = get_db()
    db.execute("DELETE FROM professional_aanmeldingen WHERE id = %s", (professional_id,))
    db.commit()
    flash("Aanmelding verwijderd.", "admin-success")
    return redirect(url_for("admin_professionals"))


@app.route("/admin/professionals/<int:professional_id>/cv")
@admin_required
def admin_download_professional_cv(professional_id):
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (professional_id,)
    ).fetchone()
    if professional is None or not professional["cv_bestandsnaam"]:
        abort(404)
    return send_from_directory(
        UPLOAD_FOLDER,
        professional["cv_bestandsnaam"],
        as_attachment=request.args.get("download") == "1",
        download_name=professional["cv_originele_naam"],
    )


@app.route("/admin/professionals/<int:professional_id>/foto")
@admin_required
def admin_professional_foto(professional_id):
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (professional_id,)
    ).fetchone()
    if professional is None or not professional["profielfoto_bestandsnaam"]:
        abort(404)
    return send_from_directory(UPLOAD_FOLDER, professional["profielfoto_bestandsnaam"])


@app.route("/admin/opdrachten/<int:opdracht_id>/sollicitaties")
@admin_required
def admin_sollicitaties(opdracht_id):
    db = get_db()
    opdracht = db.execute("SELECT * FROM opdrachten WHERE id = %s", (opdracht_id,)).fetchone()
    if opdracht is None:
        abort(404)
    sollicitaties = db.execute(
        "SELECT * FROM sollicitaties WHERE opdracht_id = %s ORDER BY id DESC", (opdracht_id,)
    ).fetchall()

    # JSON-velden alvast omzetten naar Python-structuren, handig voor de template
    verwerkt = []
    for s in sollicitaties:
        verwerkt.append({
            **dict(s),
            "functies": json.loads(s["functies_json"]),
            "eis_antwoorden": json.loads(s["eis_antwoorden_json"]),
            "wens_antwoorden": json.loads(s["wens_antwoorden_json"]),
        })

    return render_template("admin_sollicitaties.html", opdracht=opdracht, sollicitaties=verwerkt)


def _sollicitatie_formvelden(opdracht, sollicitatie=None):
    """Verzamelt de formuliervelden voor het handmatig aanmaken/wijzigen van een
    sollicitatie, inclusief validatie. Retourneert (data_dict, fouten_lijst)."""
    naam = request.form.get("naam", "").strip()
    email = request.form.get("email", "").strip()
    telefoon = request.form.get("telefoon", "").strip()
    linkedin = request.form.get("linkedin", "").strip()
    functies = [f for f in request.form.getlist("functies") if f in FUNCTIES][:MAX_FUNCTIES]
    motivatie = request.form.get("motivatie", "").strip()

    eisen = json.loads(opdracht["eisen_json"])
    wensen = json.loads(opdracht["wensen_json"])
    eis_antwoorden = [
        {"vraag": eis, "antwoord": request.form.get(f"eis_{i}", "").strip()}
        for i, eis in enumerate(eisen)
    ]
    wens_antwoorden = [
        {"vraag": wens, "antwoord": request.form.get(f"wens_{i}", "").strip()}
        for i, wens in enumerate(wensen)
    ]

    fouten = []
    if not naam:
        fouten.append("naam")
    if not email or not is_valid_email(email):
        fouten.append("een geldig e-mailadres")
    if not motivatie:
        fouten.append("motivatie")

    return {
        "naam": naam, "email": email, "telefoon": telefoon, "linkedin": linkedin,
        "functies": functies, "motivatie": motivatie,
        "eis_antwoorden": eis_antwoorden, "wens_antwoorden": wens_antwoorden,
    }, fouten


@app.route("/admin/opdrachten/<int:opdracht_id>/sollicitaties/nieuw", methods=["GET", "POST"])
@admin_required
def admin_sollicitatie_nieuw(opdracht_id):
    db = get_db()
    opdracht = db.execute("SELECT * FROM opdrachten WHERE id = %s", (opdracht_id,)).fetchone()
    if opdracht is None:
        abort(404)

    eisen = json.loads(opdracht["eisen_json"])
    wensen = json.loads(opdracht["wensen_json"])

    if request.method == "POST":
        data, fouten = _sollicitatie_formvelden(opdracht)
        cv_bestand = request.files.get("cv")

        if not cv_bestand or cv_bestand.filename == "":
            fouten.append("een cv")
        elif not allowed_cv_file(cv_bestand.filename):
            flash("Upload het cv als PDF, DOC of DOCX.", "admin-error")
            return redirect(url_for("admin_sollicitatie_nieuw", opdracht_id=opdracht_id))

        if fouten:
            flash(f"Vul in elk geval het volgende in: {', '.join(fouten)}.", "admin-error")
            return redirect(url_for("admin_sollicitatie_nieuw", opdracht_id=opdracht_id))

        originele_naam = secure_filename(cv_bestand.filename)
        extensie = originele_naam.rsplit(".", 1)[1].lower()
        opgeslagen_naam = f"{uuid.uuid4().hex}.{extensie}"
        cv_bestand.save(os.path.join(UPLOAD_FOLDER, opgeslagen_naam))

        db.execute(
            """
            INSERT INTO sollicitaties
                (opdracht_id, aangemaakt_op, naam, email, telefoon, linkedin, functies_json,
                 cv_bestandsnaam, cv_originele_naam, motivatie, eis_antwoorden_json, wens_antwoorden_json,
                 status, volg_token)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                opdracht_id,
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                data["naam"], data["email"], data["telefoon"], data["linkedin"],
                json.dumps(data["functies"]), opgeslagen_naam, originele_naam,
                data["motivatie"], json.dumps(data["eis_antwoorden"]), json.dumps(data["wens_antwoorden"]),
                "in behandeling", uuid.uuid4().hex,
            ),
        )
        db.commit()
        flash(f"Sollicitatie van {data['naam']} toegevoegd.", "admin-success")
        return redirect(url_for("admin_sollicitaties", opdracht_id=opdracht_id))

    return render_template(
        "admin_sollicitatie_form.html",
        opdracht=opdracht, eisen=eisen, wensen=wensen,
        functies=FUNCTIES, max_functies=MAX_FUNCTIES,
        sollicitatie=None, modus="nieuw",
    )


@app.route("/admin/sollicitaties/<int:sollicitatie_id>/wijzigen", methods=["GET", "POST"])
@admin_required
def admin_sollicitatie_wijzigen(sollicitatie_id):
    db = get_db()
    sollicitatie = db.execute("SELECT * FROM sollicitaties WHERE id = %s", (sollicitatie_id,)).fetchone()
    if sollicitatie is None:
        abort(404)
    opdracht = db.execute("SELECT * FROM opdrachten WHERE id = %s", (sollicitatie["opdracht_id"],)).fetchone()

    eisen = json.loads(opdracht["eisen_json"])
    wensen = json.loads(opdracht["wensen_json"])

    if request.method == "POST":
        data, fouten = _sollicitatie_formvelden(opdracht)
        cv_bestand = request.files.get("cv")

        opgeslagen_naam = sollicitatie["cv_bestandsnaam"]
        originele_naam = sollicitatie["cv_originele_naam"]

        if cv_bestand and cv_bestand.filename:
            if not allowed_cv_file(cv_bestand.filename):
                flash("Upload het cv als PDF, DOC of DOCX.", "admin-error")
                return redirect(url_for("admin_sollicitatie_wijzigen", sollicitatie_id=sollicitatie_id))
            oud_pad = os.path.join(UPLOAD_FOLDER, sollicitatie["cv_bestandsnaam"])
            if os.path.isfile(oud_pad):
                os.remove(oud_pad)
            originele_naam = secure_filename(cv_bestand.filename)
            extensie = originele_naam.rsplit(".", 1)[1].lower()
            opgeslagen_naam = f"{uuid.uuid4().hex}.{extensie}"
            cv_bestand.save(os.path.join(UPLOAD_FOLDER, opgeslagen_naam))

        if fouten:
            flash(f"Vul in elk geval het volgende in: {', '.join(fouten)}.", "admin-error")
            return redirect(url_for("admin_sollicitatie_wijzigen", sollicitatie_id=sollicitatie_id))

        db.execute(
            """
            UPDATE sollicitaties SET
                naam = %s, email = %s, telefoon = %s, linkedin = %s, functies_json = %s,
                cv_bestandsnaam = %s, cv_originele_naam = %s, motivatie = %s,
                eis_antwoorden_json = %s, wens_antwoorden_json = %s
            WHERE id = %s
            """,
            (
                data["naam"], data["email"], data["telefoon"], data["linkedin"],
                json.dumps(data["functies"]), opgeslagen_naam, originele_naam,
                data["motivatie"], json.dumps(data["eis_antwoorden"]), json.dumps(data["wens_antwoorden"]),
                sollicitatie_id,
            ),
        )
        db.commit()
        flash(f"Sollicitatie van {data['naam']} bijgewerkt.", "admin-success")
        return redirect(url_for("admin_sollicitaties", opdracht_id=opdracht["id"]))

    sollicitatie_voor_template = {
        **dict(sollicitatie),
        "functies": json.loads(sollicitatie["functies_json"]),
        "eis_antwoorden": json.loads(sollicitatie["eis_antwoorden_json"]),
        "wens_antwoorden": json.loads(sollicitatie["wens_antwoorden_json"]),
    }

    return render_template(
        "admin_sollicitatie_form.html",
        opdracht=opdracht, eisen=eisen, wensen=wensen,
        functies=FUNCTIES, max_functies=MAX_FUNCTIES,
        sollicitatie=sollicitatie_voor_template, modus="wijzigen",
    )


@app.route("/admin/sollicitaties/<int:sollicitatie_id>/verwijderen", methods=["POST"])
@admin_required
def admin_sollicitatie_verwijderen(sollicitatie_id):
    db = get_db()
    sollicitatie = db.execute("SELECT * FROM sollicitaties WHERE id = %s", (sollicitatie_id,)).fetchone()
    if sollicitatie is None:
        abort(404)
    opdracht_id = sollicitatie["opdracht_id"]

    cv_pad = os.path.join(UPLOAD_FOLDER, sollicitatie["cv_bestandsnaam"])
    if os.path.isfile(cv_pad):
        os.remove(cv_pad)

    db.execute("DELETE FROM sollicitaties WHERE id = %s", (sollicitatie_id,))
    db.commit()
    flash("Sollicitatie verwijderd.", "admin-success")
    return redirect(url_for("admin_sollicitaties", opdracht_id=opdracht_id))


@app.route("/admin/sollicitaties/<int:sollicitatie_id>/status", methods=["POST"])
@admin_required
def admin_sollicitatie_status(sollicitatie_id):
    nieuwe_status = request.form.get("status", "").strip()
    if nieuwe_status not in SOLLICITATIE_STATUSSEN:
        flash("Onbekende status.", "admin-error")
        return redirect(request.referrer or url_for("admin_dashboard"))

    db = get_db()
    sollicitatie = db.execute("SELECT * FROM sollicitaties WHERE id = %s", (sollicitatie_id,)).fetchone()
    if sollicitatie is None:
        abort(404)

    db.execute("UPDATE sollicitaties SET status = %s WHERE id = %s", (nieuwe_status, sollicitatie_id))
    db.commit()
    flash(f"Status van {sollicitatie['naam']} bijgewerkt naar '{nieuwe_status}'.", "admin-success")
    return redirect(url_for("admin_sollicitaties", opdracht_id=sollicitatie["opdracht_id"]))


@app.route("/sollicitatie/status/<token>")
def sollicitatie_status(token):
    db = get_db()
    sollicitatie = db.execute(
        "SELECT * FROM sollicitaties WHERE volg_token = %s", (token,)
    ).fetchone()
    if sollicitatie is None:
        abort(404)
    opdracht = db.execute(
        "SELECT * FROM opdrachten WHERE id = %s", (sollicitatie["opdracht_id"],)
    ).fetchone()
    return render_template("sollicitatie_status.html", sollicitatie=sollicitatie, opdracht=opdracht)


# ---------------------------------------------------------------------------
# Eigen omgeving voor ingelogde professionals
# ---------------------------------------------------------------------------

@app.route("/professional")
@professional_required
def professional_dashboard():
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (session["professional_id"],)
    ).fetchone()
    if professional is None:
        session.pop("professional_id", None)
        abort(404)

    eigen_sollicitaties = db.execute(
        """
        SELECT s.*, o.titel AS opdracht_titel, o.id AS opdracht_id
        FROM sollicitaties s
        JOIN opdrachten o ON o.id = s.opdracht_id
        WHERE s.email = %s
        ORDER BY s.id DESC
        """,
        (professional["email"],),
    ).fetchall()

    professional_weergave = {**dict(professional), "tags": json.loads(professional["tags_json"] or "[]")}

    return render_template(
        "professional_dashboard.html",
        professional=professional_weergave,
        sollicitaties=eigen_sollicitaties,
    )


@app.route("/professional/profiel", methods=["GET", "POST"])
@professional_required
def professional_profiel_wijzigen():
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (session["professional_id"],)
    ).fetchone()
    if professional is None:
        session.pop("professional_id", None)
        abort(404)

    if request.method == "POST":
        naam = request.form.get("naam", "").strip()
        telefoon = request.form.get("telefoon", "").strip()
        linkedin = request.form.get("linkedin", "").strip()
        woonplaats = request.form.get("woonplaats", "").strip()
        vakgebied = request.form.get("vakgebied", "").strip()
        ervaring = request.form.get("ervaring", "").strip()
        beschikbaarheid = request.form.get("beschikbaarheid", "").strip()
        toelichting = request.form.get("toelichting", "").strip()
        voorkeur = request.form.get("voorkeur", "beide").strip()
        if voorkeur not in ("zzp", "detachering", "beide"):
            voorkeur = "beide"
        tags_ruw = request.form.get("tags", "").strip()
        tags = [t.strip() for t in tags_ruw.split(",") if t.strip()][:30]

        if not naam or not vakgebied:
            flash("Naam en vakgebied zijn verplicht.", "professional-profiel-error")
            return redirect(url_for("professional_profiel_wijzigen"))

        cv_bestandsnaam = professional["cv_bestandsnaam"]
        cv_originele_naam = professional["cv_originele_naam"]
        cv_bestand = request.files.get("cv")
        if cv_bestand and cv_bestand.filename:
            if not allowed_cv_file(cv_bestand.filename):
                flash("Upload je cv als PDF, DOC of DOCX.", "professional-profiel-error")
                return redirect(url_for("professional_profiel_wijzigen"))
            if cv_bestandsnaam:
                oud_pad = os.path.join(UPLOAD_FOLDER, cv_bestandsnaam)
                if os.path.isfile(oud_pad):
                    os.remove(oud_pad)
            cv_originele_naam = secure_filename(cv_bestand.filename)
            extensie = cv_originele_naam.rsplit(".", 1)[1].lower()
            cv_bestandsnaam = f"{uuid.uuid4().hex}.{extensie}"
            cv_bestand.save(os.path.join(UPLOAD_FOLDER, cv_bestandsnaam))

        foto_bestandsnaam = professional["profielfoto_bestandsnaam"]
        foto_originele_naam = professional["profielfoto_originele_naam"]
        foto_bestand = request.files.get("profielfoto")
        if foto_bestand and foto_bestand.filename:
            if not allowed_image_file(foto_bestand.filename):
                flash("Upload je profielfoto als JPG, PNG of WEBP.", "professional-profiel-error")
                return redirect(url_for("professional_profiel_wijzigen"))
            if foto_bestandsnaam:
                oud_pad = os.path.join(UPLOAD_FOLDER, foto_bestandsnaam)
                if os.path.isfile(oud_pad):
                    os.remove(oud_pad)
            foto_originele_naam = secure_filename(foto_bestand.filename)
            extensie = foto_originele_naam.rsplit(".", 1)[1].lower()
            foto_bestandsnaam = f"{uuid.uuid4().hex}.{extensie}"
            foto_bestand.save(os.path.join(UPLOAD_FOLDER, foto_bestandsnaam))

        db.execute(
            """
            UPDATE professional_aanmeldingen SET
                naam = %s, telefoon = %s, linkedin = %s, woonplaats = %s, vakgebied = %s,
                ervaring = %s, beschikbaarheid = %s, toelichting = %s, voorkeur = %s, tags_json = %s,
                cv_bestandsnaam = %s, cv_originele_naam = %s,
                profielfoto_bestandsnaam = %s, profielfoto_originele_naam = %s
            WHERE id = %s
            """,
            (
                naam, telefoon, linkedin, woonplaats, vakgebied, ervaring, beschikbaarheid,
                toelichting, voorkeur, json.dumps(tags), cv_bestandsnaam, cv_originele_naam,
                foto_bestandsnaam, foto_originele_naam, professional["id"],
            ),
        )
        db.commit()
        flash("Je profiel is bijgewerkt.", "professional-profiel-success")
        return redirect(url_for("professional_dashboard"))

    professional_weergave = {**dict(professional), "tags": json.loads(professional["tags_json"] or "[]")}
    return render_template("professional_profiel_form.html", professional=professional_weergave)


@app.route("/professional/cv")
@professional_required
def professional_eigen_cv():
    db = get_db()
    professional = db.execute(
        "SELECT * FROM professional_aanmeldingen WHERE id = %s", (session["professional_id"],)
    ).fetchone()
    if professional is None or not professional["cv_bestandsnaam"]:
        abort(404)
    return send_from_directory(
        UPLOAD_FOLDER, professional["cv_bestandsnaam"],
        as_attachment=request.args.get("download") == "1", download_name=professional["cv_originele_naam"],
    )


@app.route("/admin/sollicitaties/<int:sollicitatie_id>/cv")
@admin_required
def admin_download_cv(sollicitatie_id):
    db = get_db()
    sollicitatie = db.execute(
        "SELECT * FROM sollicitaties WHERE id = %s", (sollicitatie_id,)
    ).fetchone()
    if sollicitatie is None:
        abort(404)
    return send_from_directory(
        UPLOAD_FOLDER,
        sollicitatie["cv_bestandsnaam"],
        as_attachment=request.args.get("download") == "1",
        download_name=sollicitatie["cv_originele_naam"],
    )


@app.errorhandler(400)
def bad_request(error):
    flash(
        str(error.description) if error.description else "Er ging iets mis. Probeer het opnieuw.",
        "form-error",
    )
    return redirect(request.referrer or url_for("index"))


if __name__ == "__main__":
    init_db()
    seed_dummy_opdrachten()
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
else:
    # Ensure the tables exist when run under a WSGI server (e.g. gunicorn) too.
    init_db()
    seed_dummy_opdrachten()
