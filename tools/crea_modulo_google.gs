/**
 * Crea il modulo "Segnalazione ritrovamento funghi" e il foglio delle risposte.
 * Uso: vai su script.google.com > Nuovo progetto > incolla questo codice > Esegui "creaModulo"
 * (la prima volta Google chiede i permessi: Consenti). I link compaiono in Visualizza > Log.
 */
function creaModulo() {
  const ZONE = ["Castagneto piccolo - Santa Cristina", "Castagneto grande - Piana", "Lago di Piana – area attrezzata", "Casa Norina - Piana", "Poggio San Francesco", "Crocifisso - Ficuzza", "Grotta Ammucciata - Ficuzza", "Cozzo Tondo - Ficuzza", "Pedale/San Giorgio - Collesano", "Piano Torre - Collesano", "Carcara/Purtedda - Caronia"];

  const form = FormApp.create('Segnalazione ritrovamento funghi');
  form.setDescription(
    'Compila una scheda per ogni fungo trovato. Per le uscite senza funghi basta la prima pagina.\n' +
    'Le determinazioni sono validate dal gruppo prima di comparire nel sito: nessuna indicazione sulla commestibilita.');
  form.setConfirmationMessage('Grazie! La segnalazione e stata registrata e sara controllata prima di essere pubblicata.');
  form.setCollectEmail(false);
  form.setAllowResponseEdits(false);

  // ---- Pagina 1: chi, quando, dove, esito ----
  form.addTextItem().setTitle('Chi segnala (nome o sigla)').setRequired(true)
    .setHelpText('Non viene pubblicato nel sito senza accordo.');
  form.addDateItem().setTitle('Data del ritrovamento / uscita').setRequired(true);
  form.addTimeItem().setTitle('Ora (se la ricordi)');
  form.addListItem().setTitle('Zona').setChoiceValues(ZONE.concat(['Altra zona (scrivila nelle note)'])).setRequired(true);
  const esito = form.addMultipleChoiceItem().setTitle('Esito').setRequired(true);

  // ---- Pagina 2: il fungo ----
  const pagFungo = form.addPageBreakItem().setTitle('Il fungo');
  esito.setChoices([
    esito.createChoice('Ho trovato funghi', pagFungo),
    esito.createChoice('Uscita senza funghi', FormApp.PageNavigationType.SUBMIT)
  ]);

  // La domanda "Caricamento file" non si puo creare da script: va aggiunta a mano (vedi istruzioni).
  form.addTextItem().setTitle('Posizione (solo se le foto non hanno la posizione)')
    .setHelpText('Incolla le coordinate o il link di Google Maps (tieni premuto sulla mappa > copia coordinate). Se le foto hanno il GPS attivo non serve.');
  form.addMultipleChoiceItem().setTitle('Determinazione')
    .setChoiceValues(['Certo (determinazione confermata)', 'Probabile', 'Da verificare (non so)'])
    .setRequired(true);
  form.addTextItem().setTitle('Nome della specie (se lo conosci)');
  form.addParagraphTextItem().setTitle('Note')
    .setHelpText('Su cosa cresce (terra, legno, albero vicino), odore, stadio (primordio, maturo), abbondanza, altro.');

  // ---- Foglio risposte ----
  const ss = SpreadsheetApp.create('Segnalazioni funghi (risposte)');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());

  Logger.log('MODULO (da compilare): ' + form.getPublishedUrl());
  Logger.log('MODULO (da modificare): ' + form.getEditUrl());
  Logger.log('FOGLIO RISPOSTE: ' + ss.getUrl());
}
