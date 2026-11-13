# PDFChief Updates

Öffentlicher Download- und Updatekanal für PDFChief. Der Quellcode bleibt im privaten Repository `Marcel0365/PDFChief`. Dieses Repository enthält nur den Appcast, den Veröffentlichungsworkflow und dessen Prüfung; fertige App-Bundles liegen unter [Releases](https://github.com/Marcel0365/PDFChief-updates/releases).

Die App unterstützt ab Version 0.2.1 macOS 14 Sonoma und neuer auf Apple Silicon. Die Build-Runner verwenden weiterhin ein aktuelles macOS/Xcode; das Deploymentziel und die veröffentlichten Feeds bleiben bei macOS 14. Neue Appcasts verlangen ausdrücklich `arm64`, damit die zuvor veröffentlichte universelle Version 0.2.0 auf Intel-Macs kein inkompatibles Update angeboten bekommt.

Der Appcast liegt unter `https://raw.githubusercontent.com/Marcel0365/PDFChief-updates/main/appcast.xml`. Das anfänglich leere XML wird mit der ersten Veröffentlichung durch einen signierten Feed ersetzt.

## Einmal einrichten

1. Den Inhalt dieses Ordners einschließlich `.github` und `tools` in das öffentliche Repository `Marcel0365/PDFChief-updates` kopieren und auf `main` veröffentlichen. **Nicht das private Quellrepository kopieren.**
2. Ein dediziertes SSH-Schlüsselpaar erzeugen. Den öffentlichen Schlüssel als **Deploy key mit Schreibzugriff ausschließlich für dieses Update-Repository** eintragen. Den privaten Schlüssel als Actions-Secret `UPDATES_DEPLOY_KEY` im privaten Quellrepository speichern. Kein PAT ist nötig.
3. Mit Sparkles `generate_keys` ein Ed25519-Schlüsselpaar erzeugen und sicher aufbewahren. Den öffentlichen Base64-Schlüssel als `SUPublicEDKey` in die App übernehmen und im öffentlichen Update-Repository als Actions-Variable `SPARKLE_PUBLIC_KEY` setzen. Den exportierten privaten Base64-Schlüssel als Actions-Secret `SPARKLE_PRIVATE_KEY` ausschließlich im privaten Quellrepository speichern.
4. GitHub Actions im öffentlichen Repository aktivieren und dessen `GITHUB_TOKEN` Schreibzugriff auf Repository-Inhalte erlauben. Die Workflowdatei beschränkt sich auf `contents: write`. Im privaten Repository benötigt der Releaseworkflow lediglich `contents: read` und den Deploy key.

Private Schlüssel gehören weder in dieses Repository noch in die App, Release-Dateien oder Logs. Änderungen am öffentlichen Schlüssel erfordern eine geplante Schlüsselrotation; nicht unabhängig von bereits installierten Apps austauschen.

## Eine Version veröffentlichen

Im privaten Repository `VERSION` auf eine stabile Version wie `0.2.0` setzen, die Änderung committen und den vorhandenen Commit mit `v0.2.0` taggen und pushen. Alternativ den privaten Workflow **Deliver a signed update** mit einem bereits vorhandenen Tag starten. Vorabversionen und Tags mit führenden Nullen werden nicht akzeptiert.

Der private Workflow baut und prüft mit macOS 26/Xcode 26.6. `APP_VERSION` und `APP_BUILD_VERSION` entsprechen dem Tag ohne `v`. Er baut ein Bundle ausschließlich für Apple Silicon (`arm64`) mit Deploymentziel macOS 14, erstellt mit `ditto` ein ZIP mit erhaltenen Framework-Symlinks und signiert Archiv und Appcast mit Sparkle 2.10.1. Die Schlüssel werden den offiziellen Sparkle-Werkzeugen über stdin übergeben. `tools/check-update.swift` prüft die Auslieferung zusätzlich.

Sparkles offizieller Generator erkennt die Architektur am Mach-O-Header und ergänzt `<sparkle:hardwareRequirements>arm64</sparkle:hardwareRequirements>`. Das Paketskript prüft diese Anforderung vor dem abschließenden Signieren des Feeds. Der öffentliche Validator prüft sie erneut und akzeptiert nur ein dünnes ARM64-Executable; Intel- und Universal-Binaries werden abgelehnt. Beim Übernehmen eines Vorfeeds werden alte Einträge entfernt, da Sparkles Versionslimit sonst getrennte OS-/Architekturzweige erhalten kann. Historische ZIPs bleiben in GitHub Releases.

Der einzige öffentliche Push enthält `.incoming/PDFChief-vX.Y.Z.zip`, `.incoming/appcast.xml` und `.incoming/release.json` auf einem temporären Branch `delivery/vX.Y.Z`. Dieser Branch basiert auf dem öffentlichen `main`; private Quellcodehistorie und private Tags werden nicht übertragen.

Der öffentliche Workflow prüft den Inhalt und beide Ed25519-Signaturen mit dem separat hinterlegten öffentlichen Schlüssel. Er erzeugt einen Releaseentwurf, lädt die fertigen Assets hoch und vergleicht die heruntergeladenen Dateien mit der Lieferung. Erst nach erfolgreicher Veröffentlichung aktualisiert er `main/appcast.xml`. Der öffentliche Versions-Tag zeigt auf den sauberen öffentlichen `main`-Commit, auf dem der Release aufgebaut wurde, niemals auf den Delivery-Commit. Anschließend löscht er den Delivery-Branch. Archive gelangen so nicht in die dauerhafte `main`- oder Taghistorie.

Release-Veröffentlichungen laufen nacheinander; Builds verschiedener Versionen dürfen parallel laufen. Eine verspätete ältere Version kann den aktuellen Feed nicht zurücksetzen. Ein vorhandener Delivery-Branch wird vom Quellworkflow nicht überschrieben. Bei einem Fehler den fehlgeschlagenen **öffentlichen** Workflow erneut starten: Entwürfe dürfen ergänzt werden; bereits veröffentlichte Assets werden nur bei byte-identischem Inhalt wiederverwendet. Bleibt ein fehlerhafter Delivery-Branch zur Diagnose bestehen, ist der bisherige Feed weiterhin gültig. Nicht bestätigte oder ältere Lieferungen erfordern eine bewusste Korrektur, bevor sie erneut ausgeliefert werden.

Die Auslieferung über temporäre Git-Branches setzt ZIPs unter 95 MiB voraus. Größere Apps benötigen einen anderen Transport für die fertigen Archive.

## Lokal vorbereiten

`BUILD_SCRATCH_PATH` überschreibt den Standard `~/Library/Developer/PDFChief/build`; `SPARKLE_BIN` kann auf den `bin`-Ordner der offiziellen Sparkle-Distribution zeigen. Ein bereits gebautes Bundle lässt sich ohne Veröffentlichung vorbereiten:

```sh
tools/make-release.sh v0.2.0 /absoluter/Pfad/PDFChief.app /tmp/PDFChief-delivery
```

`SPARKLE_PRIVATE_KEY` muss im Prozess gesetzt sein. Den Schlüssel nicht als Befehlsargument eingeben und kein Shell-Tracing einschalten. Das Ausgabeziel muss leer sein. Als optionales viertes Argument wird ein vorhandener Appcast als Grundlage übernommen. Der Feed enthält jeweils die neueste Version; frühere Downloads bleiben in GitHub Releases verfügbar.

## Apple-Signierung

Der aktuelle Workflow signiert **ad hoc**. Die Sparkle-Signatur schützt die Echtheit von Updates; sie ersetzt keine Apple Developer ID oder Notarisierung. Bei der ersten Installation eines heruntergeladenen Builds kann macOS deshalb eine manuelle Freigabe verlangen.

Für eine spätere notarisierten Verteilung werden ein echtes **Developer ID Application**-Zertifikat samt privatem Schlüssel, dessen P12-Passwort und Zugang zu Apples Notarisierungsdienst benötigt. Die Zertifikate gehören in Actions-Secrets und einen temporären Schlüsselbund des Runners; das passende `SIGN_IDENTITY` wird beim Build gesetzt. Anschließend mit `notarytool` prüfen, das Ticket mit `stapler` an die App hängen und erst danach ZIP und Sparkle-Appcast signieren. Die aktuelle Pipeline importiert keine Apple-Zertifikate und behauptet keine Notarisierung.

Offizielle Referenzen: [Sparkle einrichten](https://sparkle-project.org/documentation/), [Updates veröffentlichen](https://sparkle-project.org/documentation/publishing/), [Sparkle-Codesigning](https://sparkle-project.org/documentation/sandboxing/#code-signing), [GitHub Deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys), [Apple-Notarisierung](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
