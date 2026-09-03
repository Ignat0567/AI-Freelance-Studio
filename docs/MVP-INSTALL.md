# MVP: установка и зависимости

Два пути. Для показа окна приложения — installer. Для текущего live-пайплайна (Grok + Елена, День 3) — исходники.

## Путь A — Windows installer (окно Studio без Python)

Файл:

`frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe`

- Размер: ~205 МБ
- SHA-256: `B72AD863F045F7877E9BEB32826C2090D96BAFE09F73B68C1892072CD4F1EF1F`
- Дата сборки: 2026-07-20
- Бэкенд упакован внутрь (`freelancerstudio-backend.exe`). Python на машине не нужен.
- Подпись кода нет. SmartScreen может предупредить.

После установки: Пуск → **AI Freelance Studio**.

Installer **не содержит** правок Дня 1–3 (Grok как writer файлов, overlay Елены, OpenRouter). Это Internal Beta 1.0.0-beta.1.

## Путь B — текущий live MVP из исходников (рекомендуется для демо заказа)

Нужно:

| Программа | Зачем | Официальная загрузка |
|---|---|---|
| Windows 10/11 | хост | — |
| Python 3.12+ и venv | бэкенд | https://www.python.org/downloads/windows/ |
| Node.js 20+ | Electron / Vite | https://nodejs.org/en/download |
| Docker Desktop | QA-гейты в контейнере | https://www.docker.com/products/docker-desktop/ |
| Grok CLI | live writer и спеки | тот же `grok`, что в PowerShell; `grok login` |
| Ollama + `qwen2.5-coder:14b` | только если worker = Local Ollama | https://ollama.com/download/windows |

Команды один раз:

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend
npm install
cd ..
start.bat
```

Settings: **Grok Subscription** → Detect → Test → Save. **Live coding execution** → on.

## Что нужно для живого заказа (оба пути)

Даже с installer окно откроется без Python. Чтобы **Start live build** стал зелёным, чеклист в Studio требует:

1. Docker Desktop запущен (или QA на хосте, если так настроено).
2. Выбранный coding worker готов:
   - Grok: `grok login`
   - Ollama: `ollama serve` и `ollama pull qwen2.5-coder:14b`
   - Claude Code / OpenRouter — своя авторизация, ключ не светить.
3. Тублер Live в Settings.

Демо-заказ: Create Project → **Use bakery website example** → Approve brief → Approve preview → Who writes the project files = Grok → Continue? → **Start live build**. Результат: `generated_projects/`, кнопка **Open folder**.

## Ограничения installer

- Бинарь не подписан.
- Чистая машина без Docker/Grok не прогонит live QA.
- Пересборка `npm run package:win` в `frontend/` собирает новый unsigned exe; сертификат не класть в репозиторий.
