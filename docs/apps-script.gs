/**
 * AKSynxa WhatsApp Base — Google Sheet Chat Logger (Method A)
 * ------------------------------------------------------------
 * Har chat (har message, in + out) is Sheet me ek row banegi.
 * Lead banne par ek extra row aayegi jiska Stage "LEAD: ..." hoga.
 *
 * SETUP (5 steps):
 *   1. Ek nayi Google Sheet banao (sheets.google.com).
 *   2. Upar menu: Extensions → Apps Script.
 *   3. Wahan ka saara code hata kar YE pura code paste karo, Save.
 *   4. Deploy → New deployment → type "Web app" chuno.
 *      "Who has access" me "Anyone" select karo → Deploy.
 *   5. Jo Web App URL mile, use .env me daalo:
 *      GOOGLE_SHEET_WEBHOOK_URL=<wo URL>
 *      (ya bot ke /admin page me "Google Sheet Webhook URL" me bhar do)
 */

var HEADERS = ["Timestamp", "Brand", "Customer Number", "Name", "Direction", "Message", "Stage"];

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

    // Pehli baar me headers bana do
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(HEADERS);
    }

    sheet.appendRow([
      data.timestamp || "",
      data.brand || "",
      data.customer_number || "",
      data.name || "",
      data.direction || "",
      data.message || "",
      data.stage || ""
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Browser me URL khol kar check karne ke liye
function doGet() {
  return ContentService.createTextOutput("AKSynxa sheet logger is live ✅");
}
