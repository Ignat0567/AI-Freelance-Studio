export const TRANSLATIONS = {
  en: {
    appTitle: 'AI FREELANCE STUDIO', jobs: 'Jobs', accounts: 'Accounts', projects: 'Projects', configureStaff: 'Configure AI Staff',
    manageKeys: 'Manage API Keys', systemPreferences: 'System Preferences', about: 'About AI FreelancerStudio', activeStaff: 'Active Staff',
    liveStudio: 'Live Studio Blueprint Floor', newProject: 'New Manual Project', hideLog: 'Hide Log', showLog: 'Show Log', activityLog: 'Activity Log', clear: 'Clear', close: 'Close',
    allProjects: 'All Projects', noProjects: 'No projects yet.', openFolder: 'Open Folder', deleteComputer: 'Delete from computer', removeCompleted: 'Remove from completed list', resume: 'Resume',
    stage: 'Stage', push: 'Push', export: 'Export', files: 'Files', showFolder: 'Show Folder',
    appearance: 'Appearance', general: 'General', studio: 'Studio', theme: 'Theme', accentColor: 'Accent Color', animationSpeed: 'Animation Speed', fontSize: 'Font Size', language: 'Language', notifications: 'Notifications', enableNotifications: 'Enable notifications', autoSave: 'Auto Save', autoSaveProject: 'Auto-save project state', savedAutomatically: 'Settings saved automatically',
    defaultBudget: 'Default Budget', pollingInterval: 'Polling Interval', logDetail: 'Log Detail Level', globalProvider: 'Global AI Provider', globalModel: 'Global AI Model', githubIntegration: 'GitHub Integration', editorIntegration: 'Editor Integration',
    activateStudio: 'Activate AI Freelance Studio', configureTokens: 'Configure API tokens for AI agents and job search.', deleteKey: 'Delete key', savedPermanently: 'Configured externally', customProviders: 'Custom AI Providers', activateSave: 'Activate & Save Configuration', resetConfiguration: 'Reset Configuration', closeWindow: 'Close this window',
  },
  ru: {
    appTitle: 'AI FREELANCE STUDIO', jobs: 'Заказы', accounts: 'Аккаунты', projects: 'Проекты', configureStaff: 'Настроить AI-команду',
    manageKeys: 'Управление API-ключами', systemPreferences: 'Системные настройки', about: 'О AI FreelancerStudio', activeStaff: 'Активная команда',
    liveStudio: 'План студии в реальном времени', newProject: 'Новый ручной проект', hideLog: 'Скрыть лог', showLog: 'Показать лог', activityLog: 'Журнал активности', clear: 'Очистить', close: 'Закрыть',
    allProjects: 'Все проекты', noProjects: 'Проектов пока нет.', openFolder: 'Открыть папку', deleteComputer: 'Удалить с компьютера', removeCompleted: 'Удалить из списка выполненных', resume: 'Продолжить',
    stage: 'Стадия', push: 'Push', export: 'Экспорт', files: 'Файлы', showFolder: 'Показать папку',
    appearance: 'Внешний вид', general: 'Общие', studio: 'Студия', theme: 'Тема', accentColor: 'Акцентный цвет', animationSpeed: 'Скорость анимации', fontSize: 'Размер шрифта', language: 'Язык', notifications: 'Уведомления', enableNotifications: 'Включить уведомления', autoSave: 'Автосохранение', autoSaveProject: 'Автосохранять состояние проекта', savedAutomatically: 'Настройки сохраняются автоматически',
    defaultBudget: 'Бюджет по умолчанию', pollingInterval: 'Интервал обновления', logDetail: 'Детализация лога', globalProvider: 'Глобальный AI-провайдер', globalModel: 'Глобальная AI-модель', githubIntegration: 'Интеграция GitHub', editorIntegration: 'Интеграция редактора',
    activateStudio: 'Активация AI Freelance Studio', configureTokens: 'Настройте API-токены для AI-агентов и поиска заказов.', deleteKey: 'Удалить ключ', savedPermanently: 'Настроен вне Studio', customProviders: 'Свои AI-провайдеры', activateSave: 'Активировать и сохранить', resetConfiguration: 'Сбросить конфигурацию', closeWindow: 'Закрыть это окно',
  },
  de: {
    appTitle: 'AI FREELANCE STUDIO', jobs: 'Aufträge', accounts: 'Konten', projects: 'Projekte', configureStaff: 'AI-Team konfigurieren',
    manageKeys: 'API-Schlüssel verwalten', systemPreferences: 'Systemeinstellungen', about: 'Über AI FreelancerStudio', activeStaff: 'Aktives Team',
    liveStudio: 'Live-Studio-Grundriss', newProject: 'Neues manuelles Projekt', hideLog: 'Log ausblenden', showLog: 'Log anzeigen', activityLog: 'Aktivitätslog', clear: 'Leeren', close: 'Schließen',
    allProjects: 'Alle Projekte', noProjects: 'Noch keine Projekte.', openFolder: 'Ordner öffnen', deleteComputer: 'Vom Computer löschen', removeCompleted: 'Aus abgeschlossenen Projekten entfernen', resume: 'Fortsetzen',
    stage: 'Phase', push: 'Push', export: 'Export', files: 'Dateien', showFolder: 'Ordner anzeigen',
    appearance: 'Darstellung', general: 'Allgemein', studio: 'Studio', theme: 'Design', accentColor: 'Akzentfarbe', animationSpeed: 'Animationsgeschwindigkeit', fontSize: 'Schriftgröße', language: 'Sprache', notifications: 'Benachrichtigungen', enableNotifications: 'Benachrichtigungen aktivieren', autoSave: 'Automatisch speichern', autoSaveProject: 'Projektstatus automatisch speichern', savedAutomatically: 'Einstellungen werden automatisch gespeichert',
    defaultBudget: 'Standardbudget', pollingInterval: 'Aktualisierungsintervall', logDetail: 'Log-Detailgrad', globalProvider: 'Globaler AI-Anbieter', globalModel: 'Globales AI-Modell', githubIntegration: 'GitHub-Integration', editorIntegration: 'Editor-Integration',
    activateStudio: 'AI Freelance Studio aktivieren', configureTokens: 'API-Tokens für AI-Agenten und Auftragssuche konfigurieren.', deleteKey: 'Schlüssel löschen', savedPermanently: 'Extern konfiguriert', customProviders: 'Eigene AI-Anbieter', activateSave: 'Aktivieren und speichern', resetConfiguration: 'Konfiguration zurücksetzen', closeWindow: 'Dieses Fenster schließen',
  },
};

export function getLang(lang) {
  return TRANSLATIONS[lang] ? lang : 'en';
}

export function tr(lang, key) {
  const safeLang = getLang(lang);
  return TRANSLATIONS[safeLang][key] || TRANSLATIONS.en[key] || key;
}
