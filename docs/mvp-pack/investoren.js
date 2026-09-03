const pptxgen = require("pptxgenjs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const LOGO = path.join(ROOT, "AI_FreelanceStudio_Logo.png");
const UI_FORM = path.join(__dirname, "ui-shots", "ui-bakery-form.png");
const UI_RESULT = path.join(__dirname, "ui-shots", "ui-result.png");
const SHOT_ALETHIA = path.join(
  ROOT, "generated_projects",
  "Alethia-Carbon-Intelligence-order_e4b57d50-02ce-4c01-a67f-d01ed6125c17-execution_3604fb34-c97c-47c4-9451-33c",
  "delivery_screenshot.png"
);
const SHOT_BAKERY = path.join(
  ROOT, "generated_projects",
  "Harbour-Bakery-order_75da46bf-7239-4a90-81ff-88cad8466bef-execution_79e7991d-ebb3-4965-a38e-5cd",
  "delivery_screenshot.png"
);

const C = {
  bg: "F6F3EE",
  bgAlt: "EDE8DF",
  rule: "D9D2C4",
  ink: "1C1A17",
  body: "4A453E",
  mute: "8A8378",
  coral: "C9572C",
  charcoal: "1A1814",
};

const FONT_H = "Georgia";
const FONT_B = "Calibri";
const FONT_M = "Consolas";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "AI Freelance Studio — Investorenbriefing";
pres.author = "AI Freelance Studio";
pres.subject = "Lokales Windows-Studio: aus einem Satz wird ein lieferbarer Ordner.";

const W = 13.333;
const H = 7.5;
const ML = 0.7;
const PAGES = "12";

function cream(s) {
  s.background = { color: C.bg };
}
function dark(s) {
  s.background = { color: C.charcoal };
}
function header(s, left, num, onDark) {
  const mute = onDark ? "8A8378" : C.mute;
  s.addText(left, {
    x: ML, y: 0.32, w: 8.5, h: 0.28,
    fontFace: FONT_M, fontSize: 11, color: mute, charSpacing: 1.4, margin: 0,
  });
  s.addText(
    [
      { text: String(num).padStart(2, "0"), options: { color: C.coral } },
      { text: " / " + PAGES, options: { color: mute } },
    ],
    { x: W - 2.5, y: 0.32, w: 1.8, h: 0.28, fontFace: FONT_M, fontSize: 11, align: "right", margin: 0 }
  );
  s.addShape(pres.shapes.LINE, {
    x: ML, y: 0.68, w: W - ML * 2, h: 0,
    line: { color: onDark ? "3A3530" : C.rule, width: 0.75 },
  });
}
function foot(s, text, onDark) {
  s.addText(text, {
    x: ML, y: H - 0.4, w: W - ML * 2, h: 0.24,
    fontFace: FONT_B, fontSize: 12, color: onDark ? "8A8378" : C.mute, margin: 0,
  });
}
function panel(s, x, y, w, h) {
  s.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: C.bgAlt },
    line: { color: C.rule, width: 0.75 },
  });
}

// 1 Cover
{
  const s = pres.addSlide();
  cream(s);
  s.addImage({ path: LOGO, x: ML, y: 0.48, w: 0.58, h: 0.58 });
  s.addText("AI FREELANCE STUDIO", {
    x: ML + 0.72, y: 0.5, w: 8, h: 0.26, fontFace: FONT_M, fontSize: 12, color: C.mute, charSpacing: 2, margin: 0,
  });
  s.addText("INVESTORENBRIEFING  ·  SEPTEMBER 2026", {
    x: ML + 0.72, y: 0.76, w: 8, h: 0.24, fontFace: FONT_M, fontSize: 11, color: C.coral, margin: 0,
  });
  s.addText("Aus einem Satz\nwird ein Ordner.", {
    x: ML, y: 1.55, w: 5.45, h: 2.05, fontFace: FONT_H, fontSize: 36, color: C.ink, margin: 0,
  });
  s.addText("Kein Chat. Auftrag rein, Ordner raus —\nohne uns am Telefon.", {
    x: ML, y: 3.78, w: 5.4, h: 1.05, fontFace: FONT_B, fontSize: 18, color: C.body, margin: 0,
  });
  const sh = 3.9;
  const sw = sh * (1280 / 800);
  s.addImage({
    path: SHOT_BAKERY, x: W - ML - sw, y: 1.42, w: sw, h: sh,
    shadow: { type: "outer", color: "000000", blur: 14, offset: 4, angle: 90, opacity: 0.22 },
  });
  s.addText("Harbour Bakery  ·  live aus dem Studio-Fenster  ·  sechs Minuten", {
    x: ML, y: H - 0.5, w: 12, h: 0.26, fontFace: FONT_M, fontSize: 12, color: C.mute, margin: 0,
  });
}

// 2 Was ist das
{
  const s = pres.addSlide();
  cream(s);
  header(s, "WAS DAS IST", 2);
  s.addText("Ein Schreibtisch. Ein Auftrag. Eine Lieferung.", {
    x: ML, y: 0.88, w: 12, h: 0.52, fontFace: FONT_H, fontSize: 26, color: C.ink, margin: 0,
  });
  s.addText("Studio läuft auf Windows, beim Operator — nicht in der Cloud des Kunden. Man tippt, was bestellt ist. Die Software schreibt den Brief, lässt Elena die Richtung setzen, baut, prüft und legt einen Ordner hin: README, Bericht, Screenshot. Wer den Ordner bekommt, braucht uns nicht am Telefon.", {
    x: ML, y: 1.5, w: 12, h: 1.15, fontFace: FONT_B, fontSize: 16, color: C.body, margin: 0,
  });
  const bits = [
    ["Website", "Eine Datei. index.html. Kein npm, kein Server. Im Browser öffnen — fertig."],
    ["Kleine Web-App", "Vite und React. Der Auftrag, den man sonst eine Woche von Hand macht."],
    ["Telegram-Bot", "Steht im Selektor. Kommt später. Wird heute nicht verkauft."],
  ];
  bits.forEach((b, i) => {
    const x = ML + i * 4.05;
    panel(s, x, 2.88, 3.9, 3.62);
    s.addText(String(i + 1).padStart(2, "0"), {
      x: x + 0.25, y: 3.08, w: 3.4, h: 0.28, fontFace: FONT_M, fontSize: 12, color: C.coral, margin: 0,
    });
    s.addText(b[0], {
      x: x + 0.25, y: 3.46, w: 3.4, h: 0.52, fontFace: FONT_H, fontSize: 22, color: C.ink, margin: 0,
    });
    s.addText(b[1], {
      x: x + 0.25, y: 4.12, w: 3.4, h: 2.0, fontFace: FONT_B, fontSize: 16, color: C.body, margin: 0,
    });
  });
}

// 3 Für wen
{
  const s = pres.addSlide();
  cream(s);
  header(s, "FÜR WEN", 3);
  s.addText("Für den, der den Auftrag schon hat.", {
    x: ML, y: 0.88, w: 12, h: 0.52, fontFace: FONT_H, fontSize: 26, color: C.ink, margin: 0,
  });
  const who = [
    ["Freelancer", "Eine Person, die Websites und kleine Apps verkauft — und nicht drei Nächte CSS schieben will."],
    ["Kleine Studios", "Wenige Köpfe, viele Anfragen. Die Software sitzt neben dem Operator, nicht vor dem Kunden."],
    ["Nicht der Endkunde", "Der Bäcker sieht den Ordner. Er installiert Studio nicht. So soll es sein."],
  ];
  who.forEach((w, i) => {
    const y = 1.62 + i * 1.62;
    panel(s, ML, y, 12, 1.48);
    s.addShape(pres.shapes.RECTANGLE, {
      x: ML, y, w: 0.11, h: 1.48, fill: { color: C.coral }, line: { color: C.coral, width: 0 },
    });
    s.addText(w[0], {
      x: ML + 0.42, y: y + 0.2, w: 11.2, h: 0.38, fontFace: FONT_H, fontSize: 20, color: C.ink, margin: 0,
    });
    s.addText(w[1], {
      x: ML + 0.42, y: y + 0.68, w: 11.2, h: 0.55, fontFace: FONT_B, fontSize: 16, color: C.body, margin: 0,
    });
  });
}

// 4 Heute
{
  const s = pres.addSlide();
  cream(s);
  header(s, "WAS HEUTE GEHT", 4);
  s.addText("Nicht versprochen. Gelaufen.", {
    x: ML, y: 0.88, w: 12, h: 0.48, fontFace: FONT_H, fontSize: 26, color: C.ink, margin: 0,
  });
  const now = [
    ["Live aus dem Fenster", "Create Project, Brief, Elena, Start live build. Der Ordner kommt aus dem echten Lauf — nicht aus einer Attrappe."],
    ["Wer schreibt", "Grok-Abo, lokale Ollama, Claude Code, OpenCode oder OpenRouter. Vor dem Start gewählt."],
    ["Elena nach Thema", "Bäckerei wird nicht zum Klima-Brand. Jeder Brief bekommt die Atmosphäre dieser Welt."],
    ["QA, die misst", "HTTP 200, Konsole, Overflow, fps wenn eine Leinwand da ist. Schönheit ist kein Gate — Messung schon."],
    ["Ordner ohne uns", "README, Bericht, Screenshot. Open folder. Repair im selben Workspace, kein neuer Auftrag."],
    ["Preflight", "Fehlt Docker oder grok login, startet Live nicht. Die Checkliste nennt den Befehl."],
  ];
  now.forEach((row, i) => {
    const col = i % 3;
    const r = Math.floor(i / 3);
    const x = ML + col * 4.05;
    const y = 1.52 + r * 2.55;
    panel(s, x, y, 3.9, 2.4);
    s.addText(row[0], {
      x: x + 0.22, y: y + 0.2, w: 3.45, h: 0.5, fontFace: FONT_H, fontSize: 16, color: C.ink, margin: 0,
    });
    s.addText(row[1], {
      x: x + 0.22, y: y + 0.78, w: 3.45, h: 1.42, fontFace: FONT_B, fontSize: 14, color: C.body, margin: 0,
    });
  });
}

// 5 Agenten
{
  const s = pres.addSlide();
  cream(s);
  header(s, "DIE AGENTEN", 5);
  s.addText("Jeder hat eine Rolle. Keiner darf die des anderen nehmen.", {
    x: ML, y: 0.84, w: 12, h: 0.4, fontFace: FONT_H, fontSize: 22, color: C.ink, margin: 0,
  });
  const agents = [
    ["Alex", "Projektleitung. Fragt nach, bis Scope und Fertig-Kriterien stehen. Schreibt keinen Code."],
    ["Elena", "Design. Richtung und Atmosphäre. Sie hält die Komposition — sie verkauft keinen Geschmack."],
    ["Codex", "Übergabe an den Coder. Architektur, Dateien, Grenzen. Darf das MVP nicht selbst für fertig erklären."],
    ["Writer", "Grok, Ollama, Claude oder OpenRouter. Schreibt die Dateien auf die Platte."],
    ["BugCatcher", "QA. Was rot ist, geht zurück. Was grün ist, ist gemessen — nicht behauptet."],
    ["Product Judge", "Liest nur die Belege. Darf objektives Scheitern nicht wegwischen."],
  ];
  agents.forEach((a, i) => {
    const col = i % 3;
    const r = Math.floor(i / 3);
    const x = ML + col * 4.05;
    const y = 1.4 + r * 2.45;
    panel(s, x, y, 3.9, 2.28);
    s.addText(a[0], {
      x: x + 0.22, y: y + 0.2, w: 3.45, h: 0.4, fontFace: FONT_H, fontSize: 18, color: C.coral, margin: 0,
    });
    s.addText(a[1], {
      x: x + 0.22, y: y + 0.7, w: 3.45, h: 1.35, fontFace: FONT_B, fontSize: 15, color: C.body, margin: 0,
    });
  });
  foot(s, "Maya (Analyse), Sentinel (Sicherheit), Lupa (Code-Review) und Goldie (Geschäft) sitzen als Rollenverträge im Studio. Sie laufen nicht auf jedem Website-Auftrag mit.");
}

// 6 Interface Auftrag
{
  const s = pres.addSlide();
  dark(s);
  header(s, "INTERFACE  ·  AUFTRAG", 6, true);
  const ih = 5.35;
  const iw = ih * (1440 / 900);
  s.addImage({
    path: UI_FORM,
    x: (W - Math.min(iw, 11.9)) / 2,
    y: 0.88,
    w: Math.min(iw, 11.9),
    h: Math.min(iw, 11.9) * (900 / 1440),
    shadow: { type: "outer", color: "000000", blur: 16, offset: 5, angle: 90, opacity: 0.4 },
  });
  foot(s, "Der Operator schreibt den Auftrag in Alltagssprache. Harbour Bakery — so startet ein Live-Lauf.", true);
}

// 7 Interface Result
{
  const s = pres.addSlide();
  dark(s);
  header(s, "INTERFACE  ·  LIEFERUNG", 7, true);
  const ih = 5.35;
  const iw = ih * (1440 / 900);
  s.addImage({
    path: UI_RESULT,
    x: (W - Math.min(iw, 11.9)) / 2,
    y: 0.88,
    w: Math.min(iw, 11.9),
    h: Math.min(iw, 11.9) * (900 / 1440),
    shadow: { type: "outer", color: "000000", blur: 16, offset: 5, angle: 90, opacity: 0.4 },
  });
  foot(s, "succeeded. Ein Repair. Open folder. Die 45,81 $ in der Leiste sind Tokenkosten über zehn Läufe — kein Umsatz.", true);
}

// 8 Alethia
{
  const s = pres.addSlide();
  dark(s);
  header(s, "PROJEKT  ·  ALETHIA", 8, true);
  const wImg = 8.15;
  const hImg = wImg * (800 / 1280);
  s.addImage({
    path: SHOT_ALETHIA, x: ML, y: 0.95, w: wImg, h: hImg,
    shadow: { type: "outer", color: "000000", blur: 14, offset: 4, angle: 90, opacity: 0.4 },
  });
  const rx = ML + wImg + 0.38;
  const rw = W - ML - wImg - ML - 0.38;
  const factsA = [
    ["Typ", "Cinematic Website. Dunkle Klima-Welt, lebendiges Netz."],
    ["Lauf", "Live aus dem Studio-Fenster. Status: succeeded."],
    ["QA", "WebGL hält etwa 60 fps. Der Gate hat die Szene gemessen, nicht gelobt."],
    ["Was zählt", "Genau die Welt des Briefs — keine Standard-Vorlage in Beige."],
  ];
  factsA.forEach((f, i) => {
    const y = 1.05 + i * 1.28;
    s.addText(f[0], {
      x: rx, y, w: rw, h: 0.28, fontFace: FONT_M, fontSize: 11, color: C.coral, charSpacing: 1.2, margin: 0,
    });
    s.addText(f[1], {
      x: rx, y: y + 0.3, w: rw, h: 0.85, fontFace: FONT_B, fontSize: 15, color: "D5D0C6", margin: 0,
    });
  });
}

// 9 Bakery
{
  const s = pres.addSlide();
  dark(s);
  header(s, "PROJEKT  ·  HARBOUR BAKERY", 9, true);
  const wImg = 8.15;
  const hImg = wImg * (800 / 1280);
  s.addImage({
    path: SHOT_BAKERY, x: ML, y: 0.95, w: wImg, h: hImg,
    shadow: { type: "outer", color: "000000", blur: 14, offset: 4, angle: 90, opacity: 0.4 },
  });
  const rx = ML + wImg + 0.38;
  const rw = W - ML - wImg - ML - 0.38;
  const factsB = [
    ["Typ", "Website. Warm, Hafen, Brot — eine andere Welt als Alethia."],
    ["Lauf", "Rund sechs Minuten live. Ein Repair, dann succeeded."],
    ["Elena", "Hat nicht die Klima-Szene wiederverwendet. Diese Welt, nicht die andere."],
    ["Was zählt", "Zwei Themen, zwei Gesichter. Genau das, was ein Studio verkaufen kann."],
  ];
  factsB.forEach((f, i) => {
    const y = 1.05 + i * 1.28;
    s.addText(f[0], {
      x: rx, y, w: rw, h: 0.28, fontFace: FONT_M, fontSize: 11, color: C.coral, charSpacing: 1.2, margin: 0,
    });
    s.addText(f[1], {
      x: rx, y: y + 0.3, w: rw, h: 0.85, fontFace: FONT_B, fontSize: 15, color: "D5D0C6", margin: 0,
    });
  });
}

// 10 Verdienen
{
  const s = pres.addSlide();
  cream(s);
  header(s, "WO DAS GELD SITZT", 10);
  s.addText("Nicht in der Lizenzfantasie.\nIn der Lieferung.", {
    x: ML, y: 0.86, w: 12, h: 1.12, fontFace: FONT_H, fontSize: 28, color: C.ink, margin: 0,
  });
  const money = [
    ["Der Auftrag", "Der Kunde zahlt für eine Website oder eine kleine App. Studio ist das Werkzeug des Operators — nicht das Produkt des Bäckers."],
    ["Die Zeit", "Harbour Bakery: rund sechs Minuten live. Tip Splitter ist als kleine App durch die QA gekommen. Das ist Arbeitszeit, die man verkaufen kann."],
    ["Die Kosten", "Token und Abos. In der Oberfläche stehen 45,81 $ über zehn Läufe. Wer rechnet, sieht beides: Erlös vom Kunden, Verbrauch der Modelle."],
    ["Der Hebel", "Dieselbe Woche, mehr Ordner — ohne nachts CSS zu schieben. Keine erfundene Million. Zuerst muss irgendwer außer uns einmal bestellt oder bezahlt haben."],
  ];
  money.forEach((m, i) => {
    const col = i % 2;
    const r = Math.floor(i / 2);
    const x = ML + col * 6.15;
    const y = 2.16 + r * 2.28;
    panel(s, x, y, 5.95, 2.12);
    s.addText(m[0], {
      x: x + 0.25, y: y + 0.16, w: 5.45, h: 0.38, fontFace: FONT_H, fontSize: 18, color: C.coral, margin: 0,
    });
    s.addText(m[1], {
      x: x + 0.25, y: y + 0.6, w: 5.45, h: 1.32, fontFace: FONT_B, fontSize: 14, color: C.body, margin: 0,
    });
  });
}

// 11 Morgen
{
  const s = pres.addSlide();
  cream(s);
  header(s, "WAS DANACH KOMMT", 11);
  s.addText("Der Code ist da. Er ist eingefroren, bis der erste fremde Auftrag sitzt.", {
    x: ML, y: 0.88, w: 12, h: 0.62, fontFace: FONT_H, fontSize: 24, color: C.ink, margin: 0,
  });
  const fut = [
    ["Telegram-Bot", "Im Selektor als nächster Pfad. Wird verkauft, wenn Website und App halten."],
    ["Marktplatz", "Modul liegt im Repo. Nicht im Demo. Erst wenn jemand für eine Lieferung bezahlt hat."],
    ["Video, Präsentation", "Generatoren existieren. Bewusst nicht verkauft, solange der Kernordner der Engpass ist."],
    ["Sandbox, Collaboration", "Testlabor und Team-Chat sind gebaut und eingefroren. Kein zweites Produkt vor dem ersten Geld."],
    ["Signiertes Windows", "Installer gibt es. Unsigned. SmartScreen wird fragen. Signatur kommt, wenn wir sie brauchen."],
    ["Mehr Sprachen im Auftrag", "Die Oberfläche kennt schon Englisch, Russisch, Deutsch. Dieser Pitch ist Deutsch. Der Lauf war Englisch."],
  ];
  fut.forEach((f, i) => {
    const col = i % 3;
    const r = Math.floor(i / 3);
    const x = ML + col * 4.05;
    const y = 1.68 + r * 2.45;
    panel(s, x, y, 3.9, 2.28);
    s.addText(f[0], {
      x: x + 0.22, y: y + 0.2, w: 3.45, h: 0.48, fontFace: FONT_H, fontSize: 16, color: C.ink, margin: 0,
    });
    s.addText(f[1], {
      x: x + 0.22, y: y + 0.75, w: 3.45, h: 1.3, fontFace: FONT_B, fontSize: 14, color: C.body, margin: 0,
    });
  });
}

// 12 Close
{
  const s = pres.addSlide();
  cream(s);
  header(s, "SCHLUSS", 12);
  s.addImage({ path: LOGO, x: ML, y: 1.15, w: 0.7, h: 0.7 });
  s.addText("Schicken Sie den Ordner.\nNicht den Chatverlauf.", {
    x: ML, y: 2.1, w: 6.15, h: 1.55, fontFace: FONT_H, fontSize: 30, color: C.ink, margin: 0,
  });
  s.addText("Wir zeigen das Fenster. Sie sitzen daneben.\nLive auf Windows. Grok. Ein Auftrag. Der Ordner danach.", {
    x: ML, y: 3.85, w: 6.15, h: 1.2, fontFace: FONT_B, fontSize: 16, color: C.body, margin: 0,
  });
  const shClose = 3.55;
  const swClose = shClose * (1280 / 800);
  s.addImage({
    path: SHOT_BAKERY, x: W - ML - swClose, y: 1.55, w: swClose, h: shClose,
    shadow: { type: "outer", color: "000000", blur: 14, offset: 4, angle: 90, opacity: 0.22 },
  });
  s.addText("AI FREELANCE STUDIO", {
    x: ML, y: H - 0.55, w: 8, h: 0.28, fontFace: FONT_M, fontSize: 12, color: C.coral, charSpacing: 2, margin: 0,
  });
}

pres
  .writeFile({ fileName: path.join(__dirname, "AI-Freelance-Studio-Investoren.pptx") })
  .then(() => console.log("wrote investoren pptx"))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
