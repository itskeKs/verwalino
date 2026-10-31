# Deployment-Hardening für die Bewerbungs-Alpha

Datum: 2026-08-27
Status: Freigegeben
Zielhost: **<user>@<host>**
Öffentliche Domain: **verwaltung.<customer>.immo**

## 1. Ziel und Randbedingungen

Die Anwendung soll als technisch gehärtete Bewerbungs-Alpha auf einem einzelnen
VPS laufen. Sie ist öffentlich erreichbar, aber durch eine zusätzliche
HTTP-Basic-Auth-Schranke geschützt. Es werden ausschließlich Demo- oder
Testdaten verwendet.

Das Paket umfasst:

- Behebung der bestätigten Sicherheits- und Betriebs-Findings im Repository,
- einen gehärteten Single-Host-Compose-Stack,
- Einbindung in den bereits laufenden Traefik auf <host>,
- reproduzierbarere Builds und aktuelle, geprüfte Abhängigkeiten,
- automatisierte Tests, Linting, Dependency-Audits und Image-Scanning,
- einen kontrollierten Erst-Deploy mit Smoke-Test und dokumentiertem Rollback.

Ausdrücklich nicht enthalten:

- Backup-Automation, Offsite-Backups oder Restore-Tests,
- Echtdaten oder produktive Nutzung,
- fachliche oder rechtliche Freigabe,
- Änderungen an fremden Anwendungen auf dem VPS,
- Ablösung oder Neuinstallation des vorhandenen Traefik.

Der Verzicht auf Backups ist ein bewusst akzeptiertes Restrisiko. Bei Verlust
des PostgreSQL- oder Dokument-Volumes werden die Alpha-Daten nicht
wiederhergestellt.

Der VPS ist außerdem ein geteilter Docker-Host. Andere Container im gemeinsamen
Traefik-Netz sowie Benutzer mit Docker-Zugriff liegen außerhalb der
Vertrauensgrenze dieses Stacks. Die doppelte HTTP-Authentifizierung schützt die
Alpha vor normalem Internetzugriff; sie ersetzt keine Isolation durch einen
dedizierten VPS. Für den ausdrücklich begrenzten Alpha-Zweck wird dieses
Restrisiko akzeptiert. PostgreSQL und das Dokument-Volume werden nicht mit
fremden Containern geteilt.

## 2. Festgestellter Host-Zustand

<host> ist ein Debian-13-Host mit Docker und Docker Compose. Der Benutzer
<user> besitzt Zugriff auf Docker. Auf dem Host laufen bereits zahlreiche
fremde Container. Ein zentraler Traefik belegt die öffentlichen Ports 80 und
443 und verwendet den Docker-Provider sowie einen Let's-Encrypt-Resolver.

Der DNS-Zustand ist deployfähig:

- <host> zeigt auf <ip>,
- verwaltung.<customer>.immo zeigt ebenfalls auf <ip>,
- für die Alpha-Domain ist kein AAAA-Record veröffentlicht.

Traefik beantwortet HTTP bereits und leitet auf HTTPS um. Das derzeit
ausgelieferte Zertifikat ist noch nicht vertrauenswürdig, weil für die neue
Host-Regel noch kein Let's-Encrypt-Zertifikat eingerichtet wurde. Das wird mit
dem Router des Alpha-Stacks hergestellt und im öffentlichen Smoke-Test geprüft.

## 3. Zielarchitektur

~~~text
Internet
  |
  v
bestehender Traefik :80/:443
  - HTTP -> HTTPS
  - Let's Encrypt
  - Host-Regel verwaltung.<customer>.immo
  - Basic Auth
  - Rate- und Request-Body-Limits
  - Security-Header
  |
  v
FastAPI-/React-App :8000
  - kein veröffentlichter Host-Port
  - nicht privilegierter Benutzer
  - nur Runtime-Datenbankzugang
  |
  v
PostgreSQL :5432
  - ausschließlich internes Compose-Netz
  - RLS und getrennte Rollen
~~~

Die App wird an das bestehende externe Docker-Netz **traefik** angeschlossen.
Routing und Middlewares werden ausschließlich über Labels am eigenen
App-Service definiert. Die zentrale Traefik-Konfiguration und fremde Container
werden nicht verändert.

Die bereits vorhandenen zusätzlichen Traefik-Entrypoints und die
Host-Firewall-Konfiguration gehören anderen Anwendungen. Sie werden geprüft,
aber nicht verändert, weil eine Änderung deren Erreichbarkeit beeinflussen
könnte.

PostgreSQL liegt nur im internen Stack-Netz. Weder App-Port 8000 noch
Datenbank-Port 5432 werden auf dem Host veröffentlicht.

## 4. Datenbankrollen und Migrationen

Der derzeitige App-Container erhält Migrator-Zugangsdaten und benutzt diese
sogar für Login-Lookups. Das wird entfernt.

Künftig gelten drei Betriebsphasen:

1. **db** startet PostgreSQL und legt beim erstmaligen Initialisieren die
   eingeschränkte Rolle **app_rw** an.
2. Ein einmaliger **migrate**-Job erhält die Migrator-URL, führt
   **alembic upgrade head** aus und endet erfolgreich.
3. Erst danach startet **app**. Dieser Service erhält ausschließlich
   **HV_DATABASE_URL** für app_rw; Migrator- und Sync-URLs fehlen dort.

Ein separater, manuell aufgerufener Bootstrap-Job erstellt die erste
Organisation und das Alpha-Admin-Konto. Nur dieser Job erhält kurzzeitig die
Migrator-URL.

Das PostgreSQL-Init-Skript gilt nur für ein neues Volume. Eine spätere
Passwortrotation aktualisiert deshalb zuerst die Rolle innerhalb einer
kontrollierten Datenbanksitzung, danach die serverseitige .env und startet
abschließend nur die betroffenen Services neu. Dieser Ablauf wird im Runbook
dokumentiert und mit einem Login-Smoke-Test abgeschlossen.

Für Login und Session-Validierung werden eng begrenzte
**SECURITY DEFINER**-Funktionen per Migration angelegt:

- Login-Lookup liefert nur die für eine konkrete normalisierte E-Mail nötigen
  Felder und die zugehörige Rolle.
- Mehrdeutige E-Mail-Treffer werden abgewiesen statt willkürlich einem Mandanten
  zugeordnet.
- Session-Validierung prüft Benutzeraktivität, Organisation und aktuelle Rolle
  bei jedem authentifizierten Request.
- Funktionen verwenden schemaqualifizierte Objekte, einen festen search_path,
  minimale Ausführungsrechte und sind ausschließlich für app_rw ausführbar.

Damit stammt die aktuelle Rolle nicht mehr vertrauensentscheidend aus dem
Cookie. Ein deaktivierter Benutzer oder eine geänderte Mitgliedschaft wirkt
ohne Abwarten des Cookie-Ablaufs.

## 5. Authentifizierung und HTTP-Schutz

Die Alpha erhält zwei Schutzschichten:

1. Traefik Basic Auth vor der gesamten Site,
2. die bestehende Anwendungsanmeldung.

Für beide Ebenen werden getrennte, zufällig erzeugte Zugangsdaten verwendet.
Passwörter und Session-Secrets liegen ausschließlich in einer serverseitigen
.env mit Modus 0600. Sie werden nicht ins Repository, Compose-Labels oder Image
geschrieben. In Labels erscheint nur der Basic-Auth-Verifier.

Weitere Maßnahmen:

- Session-Cookie in Produktion mit Secure, HttpOnly, SameSite=Lax, Path /
  und einem __Host-Präfix,
- kurze Alpha-Sitzungsdauer und serverseitige Principal-Validierung,
- einheitliche Login-Fehler ohne Benutzer-Enumeration,
- zeitlich vergleichbare Passwortprüfung auch bei unbekannter E-Mail,
- Mindestlänge 12 und Obergrenze 128 Zeichen für neu gesetzte Passwörter,
- begrenzte Login-Rate über eine dedizierte Traefik-Route,
- Produktionsmodus ohne öffentliche OpenAPI- und Swagger-Oberfläche,
- erlaubter Host wird auf verwaltung.<customer>.immo begrenzt.

Traefik setzt mindestens HSTS nach erfolgreichem TLS-Start,
X-Content-Type-Options: nosniff, eine restriktive Referrer-Policy,
Frame-Schutz und eine zur gebauten SPA passende Content-Security-Policy.
Die endgültige CSP wird mit dem realen Frontend-Build getestet und nicht
blind als ungetestete Zeichenfolge ausgerollt.

## 6. Anwendungssicherheit

### 6.1 SPA-Dateiauslieferung

Der bestätigte Path-Traversal-Fehler wird regressionsgetrieben behoben.
Dateien dürfen nur ausgeliefert werden, wenn ihr kanonischer Pfad innerhalb
des konfigurierten Frontend-Verzeichnisses liegt. Unbekannte API-Pfade bleiben
404; gültige SPA-Routen fallen auf index.html zurück.

Tests decken normale Assets, SPA-Fallback, URL-kodierte Traversalvarianten,
Backslash-Varianten und den Zugriff auf Dateien außerhalb des
Dist-Verzeichnisses ab.

### 6.2 Datei-Uploads und Downloads

Alle Dokument-Endpunkte verwenden einen gemeinsamen begrenzten Reader:

- maximal 25 MiB je Datei,
- Lesen in begrenzten Chunks statt eines unbeschränkten Komplett-Reads,
- konsistente 413-Antwort bei Überschreitung,
- normalisierte Dateinamen und keine Ableitung eines Speicherpfads aus dem
  Client-Dateinamen,
- vorhandene Content-Addressierung und Mandantentrennung bleiben erhalten,
- Downloads werden gestreamt und als Attachment mit nosniff ausgeliefert.

Traefik setzt zusätzlich ein etwas oberhalb des App-Limits liegendes
Request-Body-Limit, damit übergroße Requests die App nicht erreichen.

### 6.3 Healthchecks

Es entstehen getrennte Endpunkte:

- Liveness prüft nur, ob der Prozess antwortet.
- Readiness prüft eine einfache Runtime-DB-Abfrage sowie Schreib- und
  Lesbarkeit des Dokument-Speicherverzeichnisses.

Der App-Container besitzt einen Docker-Healthcheck. Der Deploy gilt erst als
erfolgreich, wenn Readiness, Login und eine authentifizierte API-Abfrage
funktionieren.

## 7. Abhängigkeiten und Build

Bekannte verwundbare Laufzeitabhängigkeiten werden auf reparierte Versionen
angehoben, insbesondere React Router und WeasyPrint. Danach werden
npm audit --omit=dev, pip-audit und ein Image-Scan erneut ausgeführt.

Das Backend erhält eine eingecheckte, exakt aufgelöste Lock-Datei. Frontend und
Backend installieren im Build ausschließlich aus den Lock-Dateien. Basisimages
und sicherheitsrelevante CI-Tool-Images werden auf konkrete Versionen und,
wo praktikabel, unveränderliche Digests festgelegt.

Der Runtime-Layer enthält keine Compiler oder Build-Werkzeuge. Nicht benötigte
Python-Paketmanager werden nach der Installation aus dem finalen Image
entfernt. Die Anwendung läuft weiterhin als Benutzer appuser.

## 8. Container-Hardening

Der Produktions-Stack verwendet:

- read_only: true für den App-Root-Filesystem-Layer,
- ein kleines tmpfs für temporäre Dateien,
- cap_drop: [ALL],
- no-new-privileges:true,
- einen expliziten nicht privilegierten Benutzer,
- CPU- und RAM-Grenzen mit sinnvollen Reserven,
- begrenzte Docker-Logdateien und Rotation,
- restart: unless-stopped,
- interne Netzwerke und keine unnötigen Port-Mappings,
- Healthchecks für PostgreSQL und App.

Das Dokument-Volume bleibt als einziger schreibbarer persistenter Bereich der
App erhalten. PostgreSQL erhält sein eigenes Volume. Da Backups ausgeschlossen
sind, wird bei der Inbetriebnahme nochmals sichtbar auf das Verlustrisiko
hingewiesen.

## 9. Frontend- und CI-Qualität

Der bestehende Produktions-Build bleibt verpflichtend. Die tatsächlichen
Lint-Probleme außerhalb der Effekt-Regel werden korrigiert. Für die zahlreichen
asynchronen Lade-Effekte wird die neue
react-hooks/set-state-in-effect-Regel nicht pauschal durch riskante UI-Umbauten
erzwungen; die projektweit verwendete Fetch-Konvention wird dokumentiert und
die Regel gezielt konfiguriert. Andere Hook-Regeln bleiben aktiv.

Vitest und React Testing Library decken mindestens Login, Auth-Kontext,
API-Fehlerbehandlung und kritische Routing-/Fallback-Logik ab.

Die CI führt vor einem Image-Build aus:

- Backend-Tests gegen frisches PostgreSQL,
- Frontend-Lint,
- Frontend-Tests,
- Frontend-Produktions-Build,
- npm audit --omit=dev,
- pip-audit,
- Build und Smoke-Test des finalen Images,
- SBOM-Erzeugung und Trivy-Scan ohne bekannte hohe oder kritische
  Laufzeitlücken.

Der Deploy-Job bleibt manuell. Er verwendet ein unveränderliches Image-Tag,
führt den Migrationsjob genau einmal aus, wartet auf Readiness und bricht bei
fehlgeschlagenem Smoke-Test ab.

## 10. Deployment auf <host>

Der Stack wird unter einem eigenen Verzeichnis im Home des Deploy-Benutzers
abgelegt, damit keine Root-Rechte und keine Änderung fremder Verzeichnisse
nötig sind. Bestehende Container, Netzwerke und Ports werden vor und nach dem
Deploy verglichen.

Im lokalen Repository liegt bereits nicht eingecheckte Nutzerarbeit. Vor jedem
schreibenden Umsetzungsschritt werden Status und Baseline-Diff erneut erfasst.
Diese Änderungen werden weder versteckt, überschrieben noch verworfen; das
Hardening wird additiv darauf aufgebaut.

Erstbereitstellung:

1. DNS-Auflösung auf <ip> erneut verifizieren.
2. Quellstand ohne lokale Secrets, Git-Metadaten oder Build-Artefakte
   übertragen.
3. Stark zufällige URL-sichere DB- und Session-Secrets auf dem Host erzeugen.
4. Image auf dem x86_64-Host bauen und scannen.
5. PostgreSQL starten und Healthcheck abwarten.
6. Migration einmalig ausführen.
7. App starten und interne Readiness prüfen.
8. Alpha-Organisation und Admin über den Bootstrap-Job anlegen.
9. Traefik-Router, TLS-Zertifikat und Basic Auth öffentlich prüfen.
10. Login, authentifizierte API und SPA-Navigation als Smoke-Test prüfen.

Für den manuellen Rollback bleibt das vorherige unveränderliche Image-Tag
erhalten. Ein App-Rollback darf nur erfolgen, wenn die angewandte Migration
rückwärtskompatibel ist; andernfalls wird der Stack gestoppt und der Fehler
gezielt behoben. Es werden keine fremden Images oder Volumes bereinigt.

## 11. Abnahmekriterien

Das Hardening gilt als technisch abgeschlossen, wenn:

- der Traversal-Exploit nicht mehr auf Dateien außerhalb des SPA-Dist zugreift,
- der App-Prozess keine Migrator-Zugangsdaten besitzt,
- RLS- und Auth-Funktionstests Mandantengrenzen sowie deaktivierte Benutzer
  nachweisen,
- alle Upload-Pfade das 25-MiB-Limit erzwingen,
- Backend-Suite, Frontend-Lint, Frontend-Tests und Frontend-Build grün sind,
- Production-Dependency-Audits keine bekannten hohen oder kritischen Lücken
  melden,
- das finale Image als Nicht-Root mit den vorgesehenen
  Container-Einschränkungen läuft,
- App und Datenbank keine öffentlichen Host-Ports besitzen,
- https://verwaltung.<customer>.immo ein gültiges Zertifikat, Basic Auth,
  Security-Header und die erwartete Anwendung liefert,
- Login und ein authentifizierter API-Aufruf auf dem Zielhost funktionieren,
- alle während der Arbeit gestarteten Hilfscontainer und Prozesse beendet sind.

Die fehlende Backup- und Restore-Lösung verhindert die Alpha-Abnahme nicht,
bleibt aber ausdrücklich dokumentiert.
