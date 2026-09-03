const pptxgen = require("pptxgenjs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const LOGO = path.join(ROOT, "AI_FreelanceStudio_Logo.png");
const SHOT_ALETHIA = path.join(
  ROOT,
  "generated_projects",
  "Alethia-Carbon-Intelligence-order_e4b57d50-02ce-4c01-a67f-d01ed6125c17-execution_3604fb34-c97c-47c4-9451-33c",
  "delivery_screenshot.png"
);
const SHOT_BAKERY = path.join(
  ROOT,
  "generated_projects",
  "Harbour-Bakery-order_75da46bf-7239-4a90-81ff-88cad8466bef-execution_79e7991d-ebb3-4965-a38e-5cd",
  "delivery_screenshot.png"
);

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.title = "AI Freelance Studio — MVP";
pres.author = "AI Freelance Studio";
pres.subject = "Описание продукта, установка и зависимости";

const SW = 13.333;
const SH = 7.5;
const BG = "0E1513";
const INK = "EDE9DE";
const INK_DIM = "8A8F86";
const INK_DIMMER = "5E635B";
const GREEN = "A8D95E";
const AMBER = "E8A54A";
const HAIRLINE = "2A322E";
const CARD = "151C1A";
const FONT_MONO = "Consolas";
const FONT_SERIF = "Georgia";
const FONT_BODY = "Calibri";
const MARGIN_L = 0.6;
const MARGIN_R = 0.6;
const HEADER_Y = 0.35;
const PAGES = "10";

function addCornerMarks(slide) {
  const len = 0.2;
  const pad = 0.22;
  const col = INK_DIMMER;
  slide.addShape(pres.shapes.LINE, { x: pad, y: pad, w: len, h: 0, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: pad, y: pad, w: 0, h: len, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: SW - pad - len, y: pad, w: len, h: 0, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: SW - pad, y: pad, w: 0, h: len, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: pad, y: SH - pad, w: len, h: 0, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: pad, y: SH - pad - len, w: 0, h: len, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: SW - pad - len, y: SH - pad, w: len, h: 0, line: { color: col, width: 0.75 } });
  slide.addShape(pres.shapes.LINE, { x: SW - pad, y: SH - pad - len, w: 0, h: len, line: { color: col, width: 0.75 } });
}

function addTopBar(slide, sectionName, pageNum) {
  slide.addText(
    [
      { text: "AI_FREELANCE_STUDIO ", options: { color: INK } },
      { text: "// ", options: { color: INK_DIM } },
      { text: "MVP", options: { color: INK } },
    ],
    {
      x: MARGIN_L, y: HEADER_Y, w: 7, h: 0.32,
      fontFace: FONT_MONO, fontSize: 11, charSpacing: 2, margin: 0,
    }
  );
  slide.addText(
    [
      { text: "§ ", options: { color: INK_DIM } },
      { text: sectionName.toUpperCase() + "   ", options: { color: INK_DIM } },
      { text: String(pageNum).padStart(2, "0"), options: { color: GREEN } },
      { text: " / ", options: { color: INK_DIM } },
      { text: PAGES, options: { color: INK_DIM } },
    ],
    {
      x: SW - 5.2 - MARGIN_R, y: HEADER_Y, w: 5.2, h: 0.32,
      fontFace: FONT_MONO, fontSize: 11, charSpacing: 2,
      align: "right", margin: 0,
    }
  );
}

function addEyebrow(slide, text, x, y, w) {
  slide.addText(text, {
    x, y, w, h: 0.28,
    fontFace: FONT_MONO, fontSize: 12, color: GREEN, charSpacing: 2, margin: 0,
  });
}

function addHairline(slide, x, y, w) {
  slide.addShape(pres.shapes.LINE, {
    x, y, w, h: 0, line: { color: HAIRLINE, width: 0.75 },
  });
}

function card(slide, x, y, w, h) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: CARD },
    line: { color: HAIRLINE, width: 0.75 },
    shadow: { type: "outer", color: "000000", blur: 8, offset: 2, angle: 90, opacity: 0.25 },
  });
}

// ------------------------------------------------------------------
// 1 Cover
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  slide.addShape(pres.shapes.OVAL, {
    x: MARGIN_L, y: HEADER_Y + 0.08, w: 0.14, h: 0.14,
    fill: { color: GREEN }, line: { color: GREEN, width: 0 },
  });
  slide.addText("LIVE  ·  OPERATOR MVP", {
    x: MARGIN_L + 0.28, y: HEADER_Y, w: 5, h: 0.32,
    fontFace: FONT_MONO, fontSize: 11, color: INK_DIM, charSpacing: 2, margin: 0,
  });
  slide.addText("WINDOWS  ·  2026", {
    x: SW - 4 - MARGIN_R, y: HEADER_Y, w: 4, h: 0.32,
    fontFace: FONT_MONO, fontSize: 11, color: INK_DIM, align: "right", charSpacing: 2, margin: 0,
  });
  slide.addImage({ path: LOGO, x: MARGIN_L, y: 1.15, w: 0.72, h: 0.72 });
  addEyebrow(slide, "ПРОДУКТ / УСТАНОВКА / ЗАВИСИМОСТИ", MARGIN_L + 0.9, 1.35, 10);
  slide.addText(
    [
      { text: "AI Freelance ", options: { fontFace: FONT_SERIF, color: INK } },
      { text: "Studio", options: { fontFace: FONT_SERIF, color: GREEN, italic: true } },
    ],
    { x: MARGIN_L, y: 2.05, w: 6.5, h: 1.15, fontSize: 40, margin: 0 }
  );
  slide.addText(
    "Локальная студия: заказ обычным языком → проверенная папка проекта.\nНе чат. Сдача с отчётом, скриншотом и командой запуска.",
    { x: MARGIN_L, y: 3.35, w: 6.4, h: 1.25, fontFace: FONT_BODY, fontSize: 18, color: INK, margin: 0 }
  );
  const coverH = 3.55;
  const coverW = coverH * (1280 / 800);
  slide.addImage({
    path: SHOT_BAKERY,
    x: SW - MARGIN_R - coverW,
    y: 1.7,
    w: coverW,
    h: coverH,
    shadow: { type: "outer", color: "000000", blur: 12, offset: 3, angle: 90, opacity: 0.4 },
  });
  slide.addText("INSTALLER  ·  1.0.0-beta.1", {
    x: MARGIN_L, y: SH - 1.05, w: 6, h: 0.35,
    fontFace: FONT_MONO, fontSize: 12, color: GREEN, charSpacing: 1, margin: 0,
  });
  slide.addText("Python для окна не нужен. Live-заказ — Docker + Grok CLI.", {
    x: MARGIN_L, y: SH - 0.7, w: 10, h: 0.32,
    fontFace: FONT_BODY, fontSize: 14, color: INK_DIM, margin: 0,
  });
}

// ------------------------------------------------------------------
// 2 Problem
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "задача", 2);
  addEyebrow(slide, "ПОЧЕМУ ЭТО СУЩЕСТВУЕТ", MARGIN_L, 0.85, 10);
  slide.addText("Модель умеет говорить. Клиенту нужна папка.", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.7, fontFace: FONT_SERIF, fontSize: 28, color: INK, margin: 0,
  });
  const cols = [
    { t: "Чат", d: "Код в переписке, без гейта, без скрина, без «как открыть»." },
    { t: "Конструктор", d: "Шаблон на все темы. Пекарня выглядит как климат-бренд." },
    { t: "Studio", d: "Бриф, Елена, live-сборка, QA, папка сдачи. Тема заказа на экране." },
  ];
  cols.forEach((c, i) => {
    const x = MARGIN_L + i * 4.05;
    card(slide, x, 2.15, 3.85, 4.4);
    slide.addText(String(i + 1).padStart(2, "0"), {
      x: x + 0.25, y: 2.35, w: 3.3, h: 0.35, fontFace: FONT_MONO, fontSize: 12, color: GREEN, margin: 0,
    });
    slide.addText(c.t, {
      x: x + 0.25, y: 2.8, w: 3.3, h: 0.55, fontFace: FONT_SERIF, fontSize: 24, color: INK, margin: 0,
    });
    slide.addText(c.d, {
      x: x + 0.25, y: 3.5, w: 3.3, h: 2.6, fontFace: FONT_BODY, fontSize: 16, color: INK_DIM, margin: 0,
    });
  });
}

// ------------------------------------------------------------------
// 3 Product
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "продукт", 3);
  addEyebrow(slide, "ЧТО СТАВИТСЯ НА СТОЛ", MARGIN_L, 0.85, 10);
  slide.addText("Десктоп на Windows. Один оператор. Один заказ до папки.", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.55, fontFace: FONT_SERIF, fontSize: 26, color: INK, margin: 0,
  });
  const items = [
    ["01", "Заказ", "Website или small web app. Текст клиента, не тикет в Jira."],
    ["02", "Елена", "Композиция под эту тему: overlay, атмосфера мира заказа."],
    ["03", "Live build", "Grok / Ollama / Claude / OpenRouter пишет файлы на диск."],
    ["04", "QA", "HTTP 200, консоль, overflow, fps если есть canvas. Не «красота»."],
    ["05", "Сдача", "README, отчёт, скриншот. Open folder. Без автора."],
    ["06", "Retry", "Тот же workspace. Не новый заказ из-за квоты."],
  ];
  items.forEach((row, i) => {
    const col = i % 3;
    const r = Math.floor(i / 3);
    const x = MARGIN_L + col * 4.05;
    const y = 1.95 + r * 2.4;
    card(slide, x, y, 3.85, 2.2);
    slide.addText(row[0], {
      x: x + 0.22, y: y + 0.18, w: 3.4, h: 0.28, fontFace: FONT_MONO, fontSize: 12, color: GREEN, margin: 0,
    });
    slide.addText(row[1], {
      x: x + 0.22, y: y + 0.5, w: 3.4, h: 0.4, fontFace: FONT_SERIF, fontSize: 20, color: INK, margin: 0,
    });
    slide.addText(row[2], {
      x: x + 0.22, y: y + 1.0, w: 3.4, h: 0.95, fontFace: FONT_BODY, fontSize: 15, color: INK_DIM, margin: 0,
    });
  });
}

// ------------------------------------------------------------------
// 4 Pipeline
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "контур", 4);
  addEyebrow(slide, "РЕШАЮЩИЙ ПУТЬ", MARGIN_L, 0.85, 10);
  slide.addText("order → brief → Elena → live → QA → folder", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.5, fontFace: FONT_MONO, fontSize: 20, color: GREEN, margin: 0,
  });
  const steps = ["Create Project", "Approve brief", "Approve preview", "Start live build", "Open folder"];
  steps.forEach((label, i) => {
    const x = MARGIN_L + i * 2.45;
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y: 2.1, w: 2.25, h: 1.15,
      fill: { color: CARD }, line: { color: HAIRLINE, width: 0.75 },
    });
    slide.addText(String(i + 1).padStart(2, "0"), {
      x: x + 0.12, y: 2.2, w: 2.0, h: 0.28, fontFace: FONT_MONO, fontSize: 11, color: GREEN, margin: 0,
    });
    slide.addText(label, {
      x: x + 0.12, y: 2.52, w: 2.0, h: 0.55, fontFace: FONT_BODY, fontSize: 14, color: INK, margin: 0,
    });
  });
  slide.addText(
    [
      { text: "Продукты в селекторе.  ", options: { breakLine: false } },
      { text: "Website", options: { color: GREEN } },
      { text: " — один index.html.  ", options: { color: INK } },
      { text: "Small web application", options: { color: GREEN } },
      { text: " — Vite/React. Telegram bot — coming later.", options: { color: INK } },
    ],
    { x: MARGIN_L, y: 3.55, w: 12, h: 0.7, fontFace: FONT_BODY, fontSize: 16, color: INK, margin: 0 }
  );
  card(slide, MARGIN_L, 4.4, 12.1, 2.35);
  slide.addText("Елена не вешает одну карточку на все заказы. Пекарня — гавань и хлеб. Климат-бренд — своя сцена. WebGL только если бриф про cinematic / 3D / живую сцену. Иначе overlay на CSS/canvas, без десятиминутного куба.", {
    x: MARGIN_L + 0.35, y: 4.6, w: 11.4, h: 1.95, fontFace: FONT_BODY, fontSize: 16, color: INK, margin: 0,
  });
}

// ------------------------------------------------------------------
// 5 Proof
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "доказательство", 5);
  addEyebrow(slide, "ЖИВЫЕ ПРОГОНЫ ИЗ ОКНА STUDIO", MARGIN_L, 0.85, 8);
  const shotH = 3.35;
  const shotW = shotH * (1280 / 800);
  slide.addImage({
    path: SHOT_ALETHIA, x: MARGIN_L, y: 1.25, w: shotW, h: shotH,
    shadow: { type: "outer", color: "000000", blur: 10, offset: 3, angle: 90, opacity: 0.35 },
  });
  slide.addImage({
    path: SHOT_BAKERY, x: MARGIN_L + shotW + 0.35, y: 1.25, w: shotW, h: shotH,
    shadow: { type: "outer", color: "000000", blur: 10, offset: 3, angle: 90, opacity: 0.35 },
  });
  slide.addText("ALETHIA  ·  cinematic website  ·  succeeded  ·  ~60 fps WebGL", {
    x: MARGIN_L, y: 4.7, w: shotW, h: 0.35, fontFace: FONT_MONO, fontSize: 11, color: GREEN, margin: 0,
  });
  slide.addText("HARBOUR BAKERY  ·  другая тема  ·  succeeded  ·  356 с, 1 ремонт", {
    x: MARGIN_L + shotW + 0.35, y: 4.7, w: shotW, h: 0.35, fontFace: FONT_MONO, fontSize: 11, color: GREEN, margin: 0,
  });
  slide.addText("Tip Splitter (small web app) тоже доходил до succeeded. Гейт не оценивает вкус — он меряет, что страница живая и открывается.", {
    x: MARGIN_L, y: 5.2, w: 12.1, h: 1.4, fontFace: FONT_BODY, fontSize: 16, color: INK_DIM, margin: 0,
  });
}

// ------------------------------------------------------------------
// 6 Installer
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "installer", 6);
  addEyebrow(slide, "ГОТОВЫЙ УСТАНОВОЧНЫЙ MVP", MARGIN_L, 0.85, 10);
  slide.addText("Windows Setup. Окно Studio без Python.", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.5, fontFace: FONT_SERIF, fontSize: 26, color: INK, margin: 0,
  });
  card(slide, MARGIN_L, 1.9, 7.6, 4.7);
  slide.addText("ФАЙЛ", {
    x: MARGIN_L + 0.3, y: 2.1, w: 7, h: 0.28, fontFace: FONT_MONO, fontSize: 12, color: GREEN, margin: 0,
  });
  slide.addText("AI Freelance Studio-Setup-1.0.0-beta.1-win.exe", {
    x: MARGIN_L + 0.3, y: 2.45, w: 7, h: 0.45, fontFace: FONT_BODY, fontSize: 16, color: INK, margin: 0,
  });
  slide.addText("frontend/installers/\n~205 МБ  ·  SHA-256 в .sha256 рядом\nБэкенд внутри: freelancerstudio-backend.exe\nПуск → AI Freelance Studio", {
    x: MARGIN_L + 0.3, y: 3.05, w: 7, h: 2.0, fontFace: FONT_BODY, fontSize: 16, color: INK_DIM, margin: 0,
  });
  slide.addText("Сборка 20.07.2026. Подписи кода нет — SmartScreen может спросить. Live Grok/Елена Дня 3 в этом exe ещё нет: для демо заказа — исходники + start.bat.", {
    x: MARGIN_L + 0.3, y: 5.15, w: 7, h: 1.2, fontFace: FONT_BODY, fontSize: 14, color: INK, margin: 0,
  });
  card(slide, 8.45, 1.9, 4.25, 4.7);
  slide.addText("ЧЕСТНО", {
    x: 8.7, y: 2.1, w: 3.8, h: 0.28, fontFace: FONT_MONO, fontSize: 12, color: AMBER, margin: 0,
  });
  slide.addText("Installer открывает приложение.\n\nLive-заказ всё равно просит Docker и Grok CLI.\n\nНовый package:win собирает unsigned exe. Сертификат в репозиторий не кладём.", {
    x: 8.7, y: 2.5, w: 3.75, h: 3.8, fontFace: FONT_BODY, fontSize: 15, color: INK, margin: 0, valign: "top",
  });
}

// ------------------------------------------------------------------
// 7 Dependencies
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "зависимости", 7);
  addEyebrow(slide, "ЧТО ДОЛЖНО СТОЯТЬ НА МАШИНЕ", MARGIN_L, 0.85, 10);
  slide.addText("Окно ≠ живой заказ. Ниже — минимум для каждого пути.", {
    x: MARGIN_L, y: 1.18, w: 12, h: 0.4, fontFace: FONT_BODY, fontSize: 16, color: INK_DIM, margin: 0,
  });
  const table = [
    [
      { text: "Программа", options: { fill: { color: "1A2320" }, color: GREEN, fontFace: FONT_MONO, fontSize: 11, bold: true } },
      { text: "Installer", options: { fill: { color: "1A2320" }, color: GREEN, fontFace: FONT_MONO, fontSize: 11, bold: true } },
      { text: "Live из исходников", options: { fill: { color: "1A2320" }, color: GREEN, fontFace: FONT_MONO, fontSize: 11, bold: true } },
      { text: "Зачем", options: { fill: { color: "1A2320" }, color: GREEN, fontFace: FONT_MONO, fontSize: 11, bold: true } },
    ],
    [
      { text: "Windows 10/11", options: { color: INK, fill: { color: CARD } } },
      { text: "да", options: { color: INK, fill: { color: CARD } } },
      { text: "да", options: { color: INK, fill: { color: CARD } } },
      { text: "хост", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
    [
      { text: "Python 3.12+", options: { color: INK, fill: { color: CARD } } },
      { text: "нет", options: { color: GREEN, fill: { color: CARD } } },
      { text: "да, venv", options: { color: INK, fill: { color: CARD } } },
      { text: "бэкенд исходников", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
    [
      { text: "Node 20+", options: { color: INK, fill: { color: CARD } } },
      { text: "нет", options: { color: GREEN, fill: { color: CARD } } },
      { text: "да", options: { color: INK, fill: { color: CARD } } },
      { text: "Electron / Vite", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
    [
      { text: "Docker Desktop", options: { color: INK, fill: { color: CARD } } },
      { text: "для live QA", options: { color: AMBER, fill: { color: CARD } } },
      { text: "да", options: { color: INK, fill: { color: CARD } } },
      { text: "гейты в контейнере", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
    [
      { text: "Grok CLI + login", options: { color: INK, fill: { color: CARD } } },
      { text: "для live writer", options: { color: AMBER, fill: { color: CARD } } },
      { text: "да", options: { color: INK, fill: { color: CARD } } },
      { text: "grok login", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
    [
      { text: "Ollama 14b", options: { color: INK, fill: { color: CARD } } },
      { text: "если этот worker", options: { color: INK_DIM, fill: { color: CARD } } },
      { text: "если этот worker", options: { color: INK_DIM, fill: { color: CARD } } },
      { text: "ollama serve / pull", options: { color: INK_DIM, fill: { color: CARD } } },
    ],
  ];
  slide.addTable(table, {
    x: MARGIN_L, y: 1.7, w: 12.1, h: 4.9,
    colW: [2.8, 2.6, 3.1, 3.6],
    border: [{ pt: 0.5, color: HAIRLINE }, { pt: 0.5, color: HAIRLINE }, { pt: 0.5, color: HAIRLINE }, { pt: 0.5, color: HAIRLINE }],
    fontFace: FONT_BODY,
    fontSize: 13,
    color: INK,
    valign: "middle",
    align: "left",
  });
}

// ------------------------------------------------------------------
// 8 Operator
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "оператор", 8);
  addEyebrow(slide, "КАК ЗАПУСТИТЬ ЗАКАЗ", MARGIN_L, 0.85, 10);
  slide.addText("Один клиент. Окно не трогать до Result.", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.45, fontFace: FONT_SERIF, fontSize: 24, color: INK, margin: 0,
  });
  const ops = [
    ["1", "start.bat  или  installer из Пуска"],
    ["2", "Settings: Grok Detect → Save. Live coding execution — on."],
    ["3", "Create Project → Website → пример пекарни или свой бриф."],
    ["4", "Approve brief → Approve preview."],
    ["5", "Who writes the project files = Grok. Continue?  Start live build."],
    ["6", "Result → Open folder. README, отчёт, скрин. Retry this workspace если упало."],
  ];
  ops.forEach((row, i) => {
    const y = 1.8 + i * 0.8;
    slide.addShape(pres.shapes.RECTANGLE, {
      x: MARGIN_L, y, w: 0.55, h: 0.62, fill: { color: GREEN }, line: { color: GREEN, width: 0 },
    });
    slide.addText(row[0], {
      x: MARGIN_L, y, w: 0.55, h: 0.62, fontFace: FONT_MONO, fontSize: 16, color: BG, align: "center", valign: "middle", margin: 0,
    });
    slide.addText(row[1], {
      x: MARGIN_L + 0.75, y, w: 11.2, h: 0.62, fontFace: FONT_BODY, fontSize: 16, color: INK, valign: "middle", margin: 0,
    });
  });
}

// ------------------------------------------------------------------
// 9 Limits
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "границы", 9);
  addEyebrow(slide, "ЧТО ЕЩЁ НЕ MVP-ЛОЖЬ", MARGIN_L, 0.85, 10);
  slide.addText("Говорим вслух, чтобы не обещать облако.", {
    x: MARGIN_L, y: 1.2, w: 12, h: 0.45, fontFace: FONT_SERIF, fontSize: 24, color: INK, margin: 0,
  });
  const limits = [
    { t: "Подпись Windows", d: "Exe unsigned. SmartScreen — ожидаемо." },
    { t: "Два контура", d: "Installer = окно июля. Live Grok/Елена = исходники." },
    { t: "First-try", d: "Bakery: 1 ремонт. Гейт не про вкус." },
    { t: "Заморозка", d: "Видео, маркетплейс, sandbox, бот — не в демо." },
    { t: "Квота", d: "Не стартовать новый заказ после быстрого provider-fail." },
    { t: "Criterion 3", d: "Нужен заказ от другого человека. Код это не закроет." },
  ];
  limits.forEach((c, i) => {
    const col = i % 3;
    const r = Math.floor(i / 3);
    const x = MARGIN_L + col * 4.05;
    const y = 1.85 + r * 2.4;
    card(slide, x, y, 3.85, 2.2);
    slide.addText(c.t, {
      x: x + 0.22, y: y + 0.25, w: 3.4, h: 0.5, fontFace: FONT_SERIF, fontSize: 18, color: GREEN, margin: 0,
    });
    slide.addText(c.d, {
      x: x + 0.22, y: y + 0.85, w: 3.4, h: 1.1, fontFace: FONT_BODY, fontSize: 15, color: INK, margin: 0,
    });
  });
}

// ------------------------------------------------------------------
// 10 Close
// ------------------------------------------------------------------
{
  const slide = pres.addSlide();
  slide.background = { color: BG };
  addCornerMarks(slide);
  addTopBar(slide, "закрытие", 10);
  slide.addImage({ path: LOGO, x: MARGIN_L, y: 1.5, w: 0.7, h: 0.7 });
  slide.addText("Папка, которую можно открыть без нас.", {
    x: MARGIN_L, y: 2.4, w: 12, h: 1.0, fontFace: FONT_SERIF, fontSize: 32, color: INK, margin: 0,
  });
  slide.addText("Документы: docs/PRODUCT.md  ·  docs/MVP-INSTALL.md\nInstaller: frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe\nДемо live: start.bat → bakery example → Grok → Start live build", {
    x: MARGIN_L, y: 3.6, w: 12, h: 1.6, fontFace: FONT_BODY, fontSize: 16, color: INK_DIM, margin: 0,
  });
  slide.addText("AI FREELANCE STUDIO  ·  MVP", {
    x: MARGIN_L, y: SH - 0.85, w: 8, h: 0.35, fontFace: FONT_MONO, fontSize: 12, color: GREEN, charSpacing: 2, margin: 0,
  });
}

pres.writeFile({ fileName: path.join(__dirname, "AI-Freelance-Studio-MVP.pptx") })
  .then(() => console.log("wrote AI-Freelance-Studio-MVP.pptx"))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
